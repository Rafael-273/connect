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
    'take-analysis', 'project-run', 'project-render', 'project-preview', 'broll-preview', 'review-preview',
    'review-render', 'preview-artifact', 'timeline-thumbnail', 'timeline-review-proxy', 'legacy-prepare', 'legacy-render', 'mastering-analyze',
    'mastering-render', 'premiere-export',
}


def enqueue_video_work(operation, primary_id, secondary_id=None):
    """Start any media task on Render and return its run ID without waiting."""
    if operation not in VIDEO_OPERATIONS:
        raise ValueError(f'Unsupported Render video operation: {operation}')
    token_operation = operation in {'preview-artifact', 'timeline-thumbnail'}
    if token_operation and (not isinstance(secondary_id, str) or not secondary_id):
        raise ValueError('A preview operation requires its lease token.')
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
        task_name = settings.RENDER_WORKFLOW_TASK
        if operation == 'take-analysis':
            prefix, separator, _ = task_name.rpartition('/')
            task_name = (prefix + separator if separator else '') + 'process_take_analysis'
        task_run = Render(token=settings.RENDER_API_KEY).workflows.start_task(
            task_name,
            [operation, int(primary_id), secondary_id if token_operation else int(secondary_id) if secondary_id is not None else None],
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
