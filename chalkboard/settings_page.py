from urllib.parse import urlsplit

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_wtf import FlaskForm
from sqlalchemy.exc import IntegrityError
from wtforms import StringField, ValidationError
from wtforms.validators import URL, AnyOf, DataRequired, Length, Regexp

from chalkboard.ai import (
    Health,
    OllamaConfig,
    client_for,
    config_for,
    record_status,
)
from chalkboard.auth import current_teacher, is_safe_next, teacher_select
from chalkboard.db import get_session
from chalkboard.models import AllowedSite
from chalkboard.sites import (
    ADD_MESSAGE,
    InvalidSite,
    normalise_domain,
    subjects_by_site,
)

THEMES = ("light", "dark", "system")
MODEL_NAME = r"^[A-Za-z0-9._:/-]+$"
MODEL_MESSAGE = "Use the model name as Ollama lists it, e.g. qwen3.5:9b"
URL_MESSAGE = "Enter an address like http://192.168.1.40:11434"

bp = Blueprint("settings", __name__, url_prefix="/settings")


class ThemeForm(FlaskForm):
    theme = StringField(validators=[AnyOf(THEMES)])
    next = StringField()


class SidebarForm(FlaskForm):
    collapsed = StringField(validators=[AnyOf(["0", "1"])])
    next = StringField()


def strip(value):
    return value.strip() if value else value


def strip_url(value):
    return value.strip().rstrip("/") if value else value


class OllamaForm(FlaskForm):
    ollama_url = StringField(
        "Server address",
        filters=[strip_url],
        validators=[
            DataRequired(),
            Length(max=255),
            URL(require_tld=False, message=URL_MESSAGE),
        ],
    )
    embed_model = StringField(
        "Embedding model",
        filters=[strip],
        validators=[
            DataRequired(),
            Length(max=100),
            Regexp(MODEL_NAME, message=MODEL_MESSAGE),
        ],
    )
    write_model = StringField(
        "Writing model",
        filters=[strip],
        validators=[
            DataRequired(),
            Length(max=100),
            Regexp(MODEL_NAME, message=MODEL_MESSAGE),
        ],
    )

    def validate_ollama_url(self, field):
        try:
            scheme = urlsplit(field.data).scheme
        except ValueError:
            raise ValidationError(URL_MESSAGE) from None
        if scheme not in ("http", "https"):
            raise ValidationError(URL_MESSAGE)

    @classmethod
    def from_config(cls, config: OllamaConfig) -> OllamaForm:
        return cls(
            formdata=None,
            data={
                "ollama_url": config.url,
                "embed_model": config.embed_model,
                "write_model": config.write_model,
            },
        )

    def config(self) -> OllamaConfig:
        return OllamaConfig(
            self.ollama_url.data, self.embed_model.data, self.write_model.data
        )


class SiteForm(FlaskForm):
    domain = StringField(
        "Add a site",
        filters=[strip],
        validators=[DataRequired(message=ADD_MESSAGE), Length(max=2048)],
    )

    def validate_domain(self, field):
        try:
            field.data = normalise_domain(field.data)
        except InvalidSite as exc:
            raise ValidationError(str(exc)) from None
        taken = get_session().scalar(
            teacher_select(AllowedSite).where(AllowedSite.domain == field.data)
        )
        if taken:
            raise ValidationError(f"{field.data} is already on the list")


class OpenModeForm(FlaskForm):
    allow_any = StringField(validators=[AnyOf(["0", "1"])])


def render_settings(
    form: OllamaForm,
    result: Health | None = None,
    tested: OllamaConfig | None = None,
    status: int = 200,
    site_form: SiteForm | None = None,
):
    return (
        render_template(
            "settings/index.html",
            active="settings",
            ollama_form=form,
            result=result,
            tested=tested,
            site_form=site_form or SiteForm(formdata=None),
            sites=get_session()
            .scalars(teacher_select(AllowedSite).order_by(AllowedSite.domain))
            .all(),
            site_subjects=subjects_by_site(current_teacher()),
        ),
        status,
    )


def run_check(config: OllamaConfig) -> Health:
    client = client_for(config)
    try:
        return client.health()
    finally:
        client.close()


def back(target: str | None, default: str):
    return redirect(target if is_safe_next(target) else default)


@bp.get("")
def show():
    form = OllamaForm.from_config(config_for(current_teacher()))
    return render_settings(form)


@bp.post("/ollama")
def ollama():
    teacher = current_teacher()
    form = OllamaForm()
    action = request.form.get("action")
    if action not in ("test", "save"):
        abort(400)
    if not form.validate_on_submit():
        return render_settings(form, status=400)
    config = form.config()
    saved = config_for(teacher)
    if action == "test":
        health = run_check(config)
        if config == saved:
            record_status(teacher, health)
            get_session().commit()
        return render_settings(form, result=health, tested=config)
    if config != saved:
        teacher.ollama_url = config.url
        teacher.embed_model = config.embed_model
        teacher.write_model = config.write_model
        teacher.ollama_status = "unknown"
    get_session().commit()
    flash("Saved.")
    return redirect(url_for("settings.show"))


@bp.post("/ollama/check")
def check_ollama():
    if not FlaskForm().validate_on_submit():
        abort(400)
    teacher = current_teacher()
    config = config_for(teacher)
    health = run_check(config)
    record_status(teacher, health)
    get_session().commit()
    form = OllamaForm.from_config(config)
    return render_settings(form, result=health, tested=config)


def sites_home():
    return redirect(url_for("settings.show", _anchor="sites-title"))


@bp.post("/sites")
def add_site():
    teacher = current_teacher()
    form = SiteForm()
    if not form.validate_on_submit():
        return render_settings(
            OllamaForm.from_config(config_for(teacher)), site_form=form, status=400
        )
    session = get_session()
    session.add(AllowedSite(teacher_id=teacher.id, domain=form.domain.data))
    try:
        session.commit()
    except IntegrityError:
        # A parallel request added the same site after the duplicate check.
        session.rollback()
        form.domain.errors.append(f"{form.domain.data} is already on the list")
        return render_settings(
            OllamaForm.from_config(config_for(teacher)), site_form=form, status=400
        )
    return sites_home()


@bp.post("/sites/<int:site_id>/delete")
def remove_site(site_id: int):
    if not FlaskForm().validate_on_submit():
        abort(400)
    session = get_session()
    site = session.scalar(teacher_select(AllowedSite).where(AllowedSite.id == site_id))
    if site is None:
        abort(404)
    session.delete(site)
    session.commit()
    return sites_home()


@bp.post("/sites/open")
def open_mode():
    form = OpenModeForm()
    if not form.validate_on_submit():
        abort(400)
    current_teacher().allow_any_site = form.allow_any.data == "1"
    get_session().commit()
    return sites_home()


@bp.post("/theme")
def theme():
    form = ThemeForm()
    if not form.validate_on_submit():
        abort(400)
    current_teacher().theme = form.theme.data
    get_session().commit()
    return back(form.next.data, url_for("settings.show"))


@bp.post("/sidebar")
def sidebar():
    form = SidebarForm()
    if not form.validate_on_submit():
        abort(400)
    current_teacher().sidebar_collapsed = form.collapsed.data == "1"
    get_session().commit()
    return back(form.next.data, url_for("subjects.index"))
