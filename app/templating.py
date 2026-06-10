"""Shared Jinja template configuration."""
import inspect
import json
from pathlib import Path

from fastapi.templating import Jinja2Templates

STATIC_DIR = Path(__file__).parent / "static"


def _static_version() -> str:
    """Cache-busting token derived from the newest static file mtime."""
    try:
        latest = max(p.stat().st_mtime for p in STATIC_DIR.rglob("*") if p.is_file())
        return str(int(latest))
    except ValueError:
        return "1"

from app.timeutils import (
    format_local_datetime,
    format_local_input,
    format_match_local_date,
    format_match_local_datetime,
    format_match_local_time,
    format_match_utc_iso_z,
    format_utc_iso_z,
)


def create_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory="app/templates")
    original_template_response = templates.TemplateResponse
    params = list(inspect.signature(original_template_response).parameters)
    request_first = bool(params and params[0] == "request")

    def template_response(*args, **kwargs):
        if args and hasattr(args[0], "scope"):
            request = args[0]
            name = args[1]
            context = dict(args[2]) if len(args) > 2 else dict(kwargs.pop("context", {}))
            context.setdefault("request", request)
            remaining = args[3:]
            if request_first:
                return original_template_response(request, name, context, *remaining, **kwargs)
            return original_template_response(name, context, *remaining, **kwargs)
        return original_template_response(*args, **kwargs)

    templates.TemplateResponse = template_response
    templates.env.filters["fromjson"] = json.loads
    templates.env.filters["local_datetime"] = format_local_datetime
    templates.env.filters["local_input"] = format_local_input
    templates.env.filters["match_date"] = format_match_local_date
    templates.env.filters["match_time"] = format_match_local_time
    templates.env.filters["match_datetime"] = format_match_local_datetime
    templates.env.filters["utc_iso"] = format_utc_iso_z
    templates.env.filters["match_utc_iso"] = format_match_utc_iso_z
    templates.env.globals["display_tz_label"] = "heure locale"
    templates.env.globals["static_version"] = _static_version()
    return templates
