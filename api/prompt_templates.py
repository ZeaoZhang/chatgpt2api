from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from api.support import require_identity
from services.prompt_template_service import prompt_template_service


class PromptTemplateBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = ""
    description: str = ""
    category: str = ""
    tags: list[str] | str | None = None
    language: str = ""
    template_text: str = Field(..., min_length=1)
    preview_image_url: str = ""


class PromptTemplateUpdateBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    description: str | None = None
    category: str | None = None
    tags: list[str] | str | None = None
    language: str | None = None
    template_text: str | None = None
    preview_image_url: str | None = None


class PromptTemplateRenderBody(BaseModel):
    template_id: str = ""
    template_text: str = ""
    variables: dict[str, object] = Field(default_factory=dict)


class MarkdownImportBody(BaseModel):
    markdown: str = Field(..., min_length=1)
    source_url: str = ""
    source_repo: str = ""
    source_path: str = ""
    tags: list[str] | str | None = None
    language: str = ""


class GithubImportBody(BaseModel):
    repo_url: str = Field(..., min_length=1)
    paths: list[str] | None = None
    tags: list[str] | str | None = None
    language: str = ""


class PromptTemplateDeleteManyBody(BaseModel):
    ids: list[str] = Field(default_factory=list)


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/prompt-templates")
    async def list_prompt_templates(
        query: str = Query(default=""),
        category: str = Query(default=""),
        tag: str = Query(default=""),
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        return await run_in_threadpool(
            prompt_template_service.list_templates,
            identity,
            query=query,
            category=category,
            tag=tag,
        )

    @router.post("/api/prompt-templates")
    async def create_prompt_template(
        body: PromptTemplateBody,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(prompt_template_service.create_template, identity, body.model_dump(mode="python"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/prompt-templates/{template_id}")
    async def get_prompt_template(template_id: str, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        item = await run_in_threadpool(prompt_template_service.get_template, identity, template_id)
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "template not found"})
        return item

    @router.put("/api/prompt-templates/{template_id}")
    async def update_prompt_template(
        template_id: str,
        body: PromptTemplateUpdateBody,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        data = {key: value for key, value in body.model_dump(mode="python").items() if value is not None}
        try:
            item = await run_in_threadpool(prompt_template_service.update_template, identity, template_id, data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "template not found"})
        return item

    @router.delete("/api/prompt-templates/{template_id}")
    async def delete_prompt_template(template_id: str, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        deleted = await run_in_threadpool(prompt_template_service.delete_template, identity, template_id)
        if not deleted:
            raise HTTPException(status_code=404, detail={"error": "template not found"})
        return {"deleted": True}

    @router.post("/api/prompt-templates/delete")
    async def delete_prompt_templates(body: PromptTemplateDeleteManyBody, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        result = await run_in_threadpool(prompt_template_service.delete_templates, identity, body.ids)
        return result

    @router.post("/api/prompt-templates/render")
    async def render_prompt_template(
        body: PromptTemplateRenderBody,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(
                prompt_template_service.render_template,
                identity,
                template_id=body.template_id,
                template_text=body.template_text,
                variables=body.variables,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.post("/api/prompt-templates/import/markdown")
    async def import_markdown_templates(
        body: MarkdownImportBody,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        return await run_in_threadpool(
            prompt_template_service.import_markdown,
            identity,
            markdown=body.markdown,
            source_url=body.source_url,
            source_repo=body.source_repo,
            source_path=body.source_path,
            tags=body.tags,
            language=body.language,
        )

    @router.post("/api/prompt-templates/import/github")
    async def import_github_templates(
        body: GithubImportBody,
        authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        try:
            return await run_in_threadpool(
                prompt_template_service.import_github,
                identity,
                repo_url=body.repo_url,
                paths=body.paths,
                tags=body.tags,
                language=body.language,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    return router
