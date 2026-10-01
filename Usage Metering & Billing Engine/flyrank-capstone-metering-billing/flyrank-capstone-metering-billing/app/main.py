import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import get_settings
from app.errors import ApiError
from app.services.jobs import Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    worker = None
    if get_settings().run_worker:
        worker = Worker()
        worker.start()
    yield
    if worker:
        worker.stop()


app = FastAPI(title="Usage Metering & Billing Engine", version="1.0.0", lifespan=lifespan)
app.include_router(router)


@app.exception_handler(ApiError)
async def api_error_handler(_: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status, headers=exc.headers,
                        content={"error": {"code": exc.code, "message": exc.message, **exc.extra}})


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError):
    problems = [{"field": ".".join(str(p) for p in e["loc"] if p != "body"), "problem": e["msg"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"error": {
        "code": "validation_error", "message": "Request validation failed.", "details": problems}})


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    logging.getLogger("metering").exception("unhandled error")  # never echo internals to clients
    return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "Internal error."}})
