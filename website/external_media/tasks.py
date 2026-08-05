from celery import shared_task

from .services import ExternalMediaPipeline, ExternalMediaProjectPipeline


@shared_task(bind=True, autoretry_for=(), name='external_media.prepare_subtitles')
def prepare_external_media(self, job_id):
    ExternalMediaPipeline().prepare_subtitles(job_id)


@shared_task(bind=True, autoretry_for=(), name='external_media.render_outputs')
def render_external_media(self, job_id):
    ExternalMediaPipeline().render_outputs(job_id)


@shared_task(bind=True, autoretry_for=(), name='external_media.run_project')
def run_external_media_project(self, project_id):
    ExternalMediaProjectPipeline().run(project_id)


@shared_task(bind=True, autoretry_for=(), name='external_media.render_project')
def render_external_media_project(self, project_id):
    ExternalMediaProjectPipeline().render(project_id)
