"""页面路由"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["pages"])

templates = Jinja2Templates(directory="web_ui/templates")


def render_page(request: Request, template_name: str, active_nav: str) -> HTMLResponse:
    """渲染页面"""
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={"request": request, "active_nav": active_nav},
    )


@router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return render_page(request, "index.html", "home")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return render_page(request, "dashboard.html", "dashboard")


@router.get("/projects", response_class=HTMLResponse)
async def projects(request: Request):
    return render_page(request, "projects.html", "projects")


@router.get("/scripts", response_class=HTMLResponse)
async def scripts(request: Request):
    return render_page(request, "scripts.html", "scripts")


@router.get("/tasks", response_class=HTMLResponse)
async def tasks(request: Request):
    return render_page(request, "tasks.html", "tasks")


@router.get("/reports", response_class=HTMLResponse)
async def reports(request: Request):
    return render_page(request, "reports.html", "reports")


@router.get("/settings", response_class=HTMLResponse)
async def settings(request: Request):
    return render_page(request, "settings.html", "settings")


@router.get("/agent", response_class=HTMLResponse)
async def agent(request: Request):
    return render_page(request, "agent.html", "agent")


@router.get("/apks", response_class=HTMLResponse)
async def apks(request: Request):
    return render_page(request, "apks.html", "apks")


@router.get("/compat-analysis", response_class=HTMLResponse)
async def compat_analysis(request: Request):
    return render_page(request, "compat_analysis.html", "compat_analysis")