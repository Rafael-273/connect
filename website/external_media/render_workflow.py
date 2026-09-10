"""Dispatch heavyweight video project jobs to Render Workflows.

The Django web process stays on the primary host.  This module is deliberately
lazy about importing the Render SDK so local development and the existing
Celery-only deployment remain usable until the integration is enabled.
"""

from __future__ import annotations

from django.conf import settings


class RenderWorkflowError(RuntimeError):
    """The remote video worker could not be started safely."""


def enabled():
    return bool(settings.RENDER_WORKFLOW_ENABLED)


VIDEO_OPERATIONS = {
    'project-run', 'project-render', 'project-preview', 'review-preview',
    'review-render', 'legacy-prepare', 'legacy-render', 'mastering-analyze',
    'mastering-render', 'premiere-export',
}


def enqueue_video_work(operation, primary_id, secondary_id=None):
    """Start any media task on Render and return its run ID without waiting."""
    if operation not in VIDEO_OPERATIONS:
        raise ValueError(f'Unsupported Render video operation: {operation}')
    if not enabled():
        return None
    if not settings.USE_S3:
        raise RenderWorkflowError(
            'O worker remoto exige USE_S3=TRUE para compartilhar os vídeos com a hospedagem principal.'
        )
    if not settings.RENDER_API_KEY or not settings.RENDER_WORKFLOW_TASK:
        raise RenderWorkflowError(
            'Defina RENDER_API_KEY e RENDER_WORKFLOW_TASK na hospedagem principal.'
        )
    try:
        from render_sdk import Render
    except ImportError as exc:
        raise RenderWorkflowError(
            'A dependência render_sdk não está instalada na hospedagem principal.'
        ) from exc

    try:
        task_run = Render(token=settings.RENDER_API_KEY).workflows.start_task(
            settings.RENDER_WORKFLOW_TASK,
            [operation, int(primary_id), int(secondary_id) if secondary_id is not None else None],
        )
    except Exception as exc:
        raise RenderWorkflowError('O Render não aceitou o job de vídeo.') from exc
    return str(task_run.id)


def enqueue_project(project_id, operation):
    operation_map = {'run': 'project-run', 'render': 'project-render'}
    try:
        return enqueue_video_work(operation_map[operation], project_id)
    except KeyError as exc:
        raise ValueError(f'Unsupported project operation: {operation}') from exc
