"""Prompt management — template rendering, versioning, A/B testing."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from jinja2 import Environment, meta, sandbox
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.domain.models import PromptTemplate, PromptVersion

logger = structlog.get_logger()

# Sandboxed Jinja2 environment (no access to Python builtins)
_jinja_env = sandbox.SandboxedEnvironment(
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)


class PromptRenderer:
    """Renders Jinja2 prompt templates with variable substitution."""

    @staticmethod
    def render(template_content: str, variables: dict[str, Any]) -> str:
        """
        Render a prompt template with variables.

        Supports:
        - Variable substitution: {{user_name}}
        - Conditionals: {% if show_context %}...{% endif %}
        - Loops: {% for item in items %}...{% endfor %}
        - Filters: {{text | upper}}, {{text | truncate(200)}}
        """
        try:
            template = _jinja_env.from_string(template_content)
            return template.render(**variables)
        except Exception as e:
            logger.error("Template render failed", error=str(e))
            raise ValueError(f"Template render error: {e}")

    @staticmethod
    def extract_variables(template_content: str) -> list[str]:
        """Extract all undefined variables from a template."""
        ast = _jinja_env.parse(template_content)
        return sorted(meta.find_undeclared_variables(ast))

    @staticmethod
    def validate(template_content: str) -> tuple[bool, str | None]:
        """Validate template syntax without rendering."""
        try:
            _jinja_env.parse(template_content)
            return True, None
        except Exception as e:
            return False, str(e)


class PromptService:
    """
    Manages prompt templates with versioning.

    Features:
    - Create/update templates
    - Version control (auto-increment)
    - Render specific version
    - Version diff comparison
    - A/B testing support (render multiple versions)
    """

    def __init__(self, session: AsyncSession) -> None:
        self._db = session
        self._renderer = PromptRenderer()

    async def create_template(
        self,
        tenant_id: uuid.UUID,
        *,
        name: str,
        content: str,
        description: str | None = None,
        variables: list[dict] | None = None,
        model_config: dict | None = None,
        app_id: uuid.UUID | None = None,
    ) -> PromptTemplate:
        """Create a new prompt template with initial version."""
        # Validate template
        valid, error = self._renderer.validate(content)
        if not valid:
            raise ValueError(f"Invalid template syntax: {error}")

        # Auto-extract variables if not provided
        if variables is None:
            var_names = self._renderer.extract_variables(content)
            variables = [
                {"name": v, "type": "string", "required": True}
                for v in var_names
            ]

        template = PromptTemplate(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            app_id=app_id,
            name=name,
            description=description,
            current_version=1,
        )
        self._db.add(template)
        await self._db.flush()

        version = PromptVersion(
            id=uuid.uuid4(),
            template_id=template.id,
            version=1,
            content=content,
            variables=variables,
            model_config_=model_config or {},
            change_note="Initial version",
        )
        self._db.add(version)
        await self._db.flush()

        return template

    async def create_version(
        self,
        template_id: uuid.UUID,
        content: str,
        *,
        tenant_id: uuid.UUID,
        change_note: str | None = None,
        variables: list[dict] | None = None,
        model_config: dict | None = None,
        created_by: str | None = None,
    ) -> PromptVersion:
        """Create a new version of an existing template."""
        stmt = select(PromptTemplate).where(
            PromptTemplate.id == template_id,
            PromptTemplate.tenant_id == tenant_id,
        )
        result = await self._db.execute(stmt)
        template = result.scalars().first()
        if not template:
            raise ValueError(f"Template {template_id} not found")

        valid, error = self._renderer.validate(content)
        if not valid:
            raise ValueError(f"Invalid template syntax: {error}")

        if variables is None:
            var_names = self._renderer.extract_variables(content)
            variables = [
                {"name": v, "type": "string", "required": True}
                for v in var_names
            ]

        new_version_num = template.current_version + 1

        version = PromptVersion(
            id=uuid.uuid4(),
            template_id=template_id,
            version=new_version_num,
            content=content,
            variables=variables,
            model_config_=model_config or template.versions[0].model_config_ if template.versions else {},
            change_note=change_note,
            created_by=created_by,
        )
        self._db.add(version)

        template.current_version = new_version_num
        await self._db.flush()

        return version

    async def render(
        self,
        template_id: uuid.UUID,
        variables: dict[str, Any],
        *,
        tenant_id: uuid.UUID,
        version: int | None = None,
    ) -> str:
        """Render a template with variables. Uses latest version if not specified."""
        if version:
            stmt = select(PromptVersion).join(PromptTemplate).where(
                PromptVersion.template_id == template_id,
                PromptVersion.version == version,
                PromptTemplate.tenant_id == tenant_id,
            )
        else:
            stmt = select(PromptTemplate).where(
                PromptTemplate.id == template_id,
                PromptTemplate.tenant_id == tenant_id,
            )
            result = await self._db.execute(stmt)
            template = result.scalars().first()
            if not template:
                raise ValueError(f"Template {template_id} not found")
            stmt = select(PromptVersion).where(
                PromptVersion.template_id == template_id,
                PromptVersion.version == template.current_version,
            )

        result = await self._db.execute(stmt)
        prompt_version = result.scalars().first()
        if not prompt_version:
            raise ValueError(f"Version not found")

        return self._renderer.render(prompt_version.content, variables)

    async def render_ab(
        self,
        template_id: uuid.UUID,
        variables: dict[str, Any],
        version_a: int,
        version_b: int,
        *,
        tenant_id: uuid.UUID,
    ) -> dict[str, str]:
        """Render two versions simultaneously for A/B testing."""
        content_a = await self.render(template_id, variables, tenant_id=tenant_id, version=version_a)
        content_b = await self.render(template_id, variables, tenant_id=tenant_id, version=version_b)
        return {"version_a": content_a, "version_b": content_b}

    async def get_versions(
        self, template_id: uuid.UUID, *, tenant_id: uuid.UUID,
    ) -> list[PromptVersion]:
        """Get all versions of a template."""
        stmt = (
            select(PromptVersion)
            .join(PromptTemplate)
            .where(
                PromptVersion.template_id == template_id,
                PromptTemplate.tenant_id == tenant_id,
            )
            .order_by(PromptVersion.version.desc())
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def diff_versions(
        self, template_id: uuid.UUID, v1: int, v2: int, *, tenant_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Compare two versions of a template."""
        versions = await self.get_versions(template_id, tenant_id=tenant_id)
        ver1 = next((v for v in versions if v.version == v1), None)
        ver2 = next((v for v in versions if v.version == v2), None)

        if not ver1 or not ver2:
            raise ValueError("One or both versions not found")

        # Simple line-by-line diff
        lines1 = ver1.content.splitlines()
        lines2 = ver2.content.splitlines()

        return {
            "version_a": v1,
            "version_b": v2,
            "content_a": ver1.content,
            "content_b": ver2.content,
            "lines_a": len(lines1),
            "lines_b": len(lines2),
            "variables_a": [v.get("name") for v in (ver1.variables or [])],
            "variables_b": [v.get("name") for v in (ver2.variables or [])],
        }
