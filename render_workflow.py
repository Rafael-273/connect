"""Render Workflow entry point for heavyweight external-media project jobs."""

from __future__ import annotations

import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'connect.settings')

import django

django.setup()

from asgiref.sync import sync_to_async
from django.conf import settings
from render_sdk import Retry, Workflows

from website.external_media.services import ExternalMediaProjectPipeline
from website.external_media.tasks import (
    _project_execution_lock,
    analyze_video_mastering,
    create_project_preview,
    create_subtitle_review_preview,
    export_premiere_project,
    master_video,
    prepare_external_media,
    render_external_media,
    render_reviewed_subtitles,
)
from website.models.external_media import ExternalMediaProject


app = Workflows()


@app.task(plan='4c-8g', timeout_seconds=21600, retry=Retry(max_retries=0, wait_duration_ms=1000))
async def process_video_work(operation: str, primary_id: int, secondary_id: int | None = None):
    """Run the synchronous Django pipeline outside Render's async event loop."""
    return await sync_to_async(_process_video_work, thread_sensitive=True)(
        operation, primary_id, secondary_id,
    )


def _process_video_work(operation: str, primary_id: int, secondary_id: int | None = None):
    """Execute every external-media task without requiring a Celery worker."""
    primary_id = int(primary_id)
    if not settings.USE_S3:
        raise RuntimeError(
            'USE_S3=TRUE é obrigatório no Render Workflow para acessar a mídia da hospedagem principal.'
        )

    if operation in {'project-run', 'project-render'}:
        with _project_execution_lock(primary_id) as acquired:
            if not acquired:
                return {'skipped': True, 'reason': 'project-already-processing'}
            project = ExternalMediaProject.objects.only('status').get(pk=primary_id)
            if project.status in {
                ExternalMediaProject.Status.FINISHED,
                ExternalMediaProject.Status.CANCELLED,
            }:
                return {'skipped': True, 'reason': 'project-is-terminal'}
            pipeline = ExternalMediaProjectPipeline()
            if operation == 'project-run':
                pipeline.run(primary_id)
            else:
                pipeline.render(primary_id)
        project.refresh_from_db(fields=['status', 'progress'])
        return {'project_id': primary_id, 'operation': operation, 'status': project.status, 'progress': project.progress}

    # These are the same task bodies used by Celery. Calling ``run`` keeps the
    # state transitions and error handling in one place while Render provides
    # the queue, compute and scale-to-zero behaviour.
    tasks = {
        'project-preview': (create_project_preview, [primary_id]),
        'review-preview': (create_subtitle_review_preview, [primary_id]),
        'review-render': (render_reviewed_subtitles, [primary_id, secondary_id]),
        'legacy-prepare': (prepare_external_media, [primary_id]),
        'legacy-render': (render_external_media, [primary_id]),
        'mastering-analyze': (analyze_video_mastering, [primary_id]),
        'mastering-render': (master_video, [primary_id]),
        'premiere-export': (export_premiere_project, [primary_id]),
    }
    try:
        task, arguments = tasks[operation]
    except KeyError as exc:
        raise ValueError(f'Unsupported video operation: {operation}') from exc
    result = task.run(*arguments)
    return {'operation': operation, 'id': primary_id, 'result': result}


if __name__ == '__main__':
    app.start()
