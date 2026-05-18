from fastapi import APIRouter

from app.api.routes.admin import router as admin_router
from app.api.routes.alerts import router as alerts_router
from app.api.routes.config import router as config_router
from app.api.routes.exports import router as exports_router
from app.api.routes.intake import router as intake_router
from app.api.routes.results import router as results_router
from app.api.routes.tasks import router as tasks_router
from app.api.routes.upload import router as upload_router
from app.api.routes.viewer import router as viewer_router
from app.api.routes.viewer_admin import router as viewer_admin_router

api_router = APIRouter()
api_router.include_router(admin_router)
api_router.include_router(upload_router)
api_router.include_router(tasks_router)
api_router.include_router(alerts_router)
api_router.include_router(config_router)
api_router.include_router(exports_router)
api_router.include_router(intake_router)
api_router.include_router(results_router)
api_router.include_router(viewer_router)
api_router.include_router(viewer_admin_router)
