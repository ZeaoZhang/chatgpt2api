from __future__ import annotations

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from api.support import require_identity, resolve_image_base_url
from services.content_filter import check_request
from services.agent_prompt_service import DEFAULT_OPTIMIZER_MODEL, agent_prompt_service
from services.image_task_service import image_task_service


class PromptOptimizeRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: str = DEFAULT_OPTIMIZER_MODEL
    mode: str = "single"
    count: int = Field(default=1, ge=1, le=20)
    size: str = ""
    template_text: str = ""
    context: str = ""


def _parse_clarification_answers(value: str | None) -> str:
    text = str(value or "").strip()
    return text


def _parse_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _agent_task_id(run_id: str, index: int) -> str:
    return f"agent-{run_id}-{index}"


def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/agent/prompt-optimize")
    async def optimize_prompt(
        body: PromptOptimizeRequest,
        authorization: str | None = Header(default=None),
    ):
        require_identity(authorization)
        try:
            return await run_in_threadpool(
                agent_prompt_service.optimize_prompt,
                prompt=body.prompt,
                model=body.model,
                mode=body.mode,
                count=body.count,
                size=body.size,
                template_text=body.template_text,
                context=body.context,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": str(exc) or "prompt optimize failed"}) from exc

    @router.post("/api/agent/image-run")
    async def run_image_agent(
        request: Request,
        authorization: str | None = Header(default=None),
        image: list[UploadFile] | None = File(default=None),
        image_list: list[UploadFile] | None = File(default=None, alias="image[]"),
        prompt: str = Form(...),
        run_id: str = Form(...),
        model: str = Form(default="gpt-image-2"),
        optimizer_model: str = Form(default=DEFAULT_OPTIMIZER_MODEL),
        mode: str = Form(default="single"),
        count: int = Form(default=1),
        size: str = Form(default=""),
        template_text: str = Form(default=""),
        clarification_answers: str = Form(default=""),
        allow_clarification: str = Form(default="true"),
    ):
        identity = require_identity(authorization)
        uploads = [*(image or []), *(image_list or [])]
        images: list[tuple[bytes, str, str]] = []
        for upload in uploads:
            image_data = await upload.read()
            if not image_data:
                raise HTTPException(status_code=400, detail={"error": "image file is empty"})
            images.append((image_data, upload.filename or "image.png", upload.content_type or "image/png"))
        try:
            safe_count = max(1, min(20, int(count or 1)))
        except (TypeError, ValueError):
            safe_count = 1
        context = _parse_clarification_answers(clarification_answers)
        try:
            optimized = await run_in_threadpool(
                agent_prompt_service.optimize_prompt,
                prompt=prompt,
                model=optimizer_model,
                mode=mode,
                count=safe_count,
                size=size,
                template_text=template_text,
                context=context,
                has_images=bool(images),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": str(exc) or "agent optimize failed"}) from exc

        if optimized.get("needs_clarification") and _parse_bool(allow_clarification):
            return {
                "status": "needs_clarification",
                "questions": optimized.get("clarification_questions") or [],
                "optimization": optimized,
            }

        final_prompt = str(optimized.get("final_prompt") or prompt).strip()
        try:
            await run_in_threadpool(check_request, final_prompt)
        except HTTPException:
            raise
        base_url = resolve_image_base_url(request)
        tasks = []
        task_mode = "edit" if images else "generate"
        for index in range(1, safe_count + 1):
            task_id = _agent_task_id(run_id, index)
            try:
                if images:
                    task = await run_in_threadpool(
                        image_task_service.submit_edit,
                        identity,
                        client_task_id=task_id,
                        prompt=final_prompt,
                        model=model,
                        size=size,
                        base_url=base_url,
                        images=images,
                    )
                else:
                    task = await run_in_threadpool(
                        image_task_service.submit_generation,
                        identity,
                        client_task_id=task_id,
                        prompt=final_prompt,
                        model=model,
                        size=size,
                        base_url=base_url,
                    )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
            tasks.append(task)

        return {
            "status": "running",
            "mode": task_mode,
            "prompt": final_prompt,
            "optimization": optimized,
            "tasks": tasks,
        }

    return router
