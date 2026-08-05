from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReframeKeyframe:
    time_seconds: float
    x: float
    y: float


@dataclass(frozen=True)
class AutoReframePlan:
    crop_width: int
    crop_height: int
    keyframes: tuple[ReframeKeyframe, ...]

    @staticmethod
    def _expression(keyframes, axis):
        if not keyframes:
            return '0'
        if len(keyframes) == 1:
            return str(round(getattr(keyframes[0], axis), 3))
        expression = str(round(getattr(keyframes[-1], axis), 3))
        for current, following in reversed(list(zip(keyframes, keyframes[1:]))):
            start = current.time_seconds
            end = following.time_seconds
            start_value = getattr(current, axis)
            end_value = getattr(following, axis)
            duration = max(0.001, end - start)
            interpolated = (
                f'{start_value:.3f}+({end_value - start_value:.3f})'
                f'*(t-{start:.3f})/{duration:.3f}'
            )
            expression = f'if(lt(t\\,{end:.3f})\\,{interpolated}\\,{expression})'
        return expression

    def ffmpeg_filters(self, width, height):
        x_expression = self._expression(self.keyframes, 'x')
        y_expression = self._expression(self.keyframes, 'y')
        return [
            f"crop={self.crop_width}:{self.crop_height}:x='{x_expression}':y='{y_expression}'",
            f'scale={width}:{height}:flags=lanczos',
        ]

    def scaled(self, x_factor, y_factor):
        return AutoReframePlan(
            crop_width=AutoReframeService._even(self.crop_width * x_factor),
            crop_height=AutoReframeService._even(self.crop_height * y_factor),
            keyframes=tuple(
                ReframeKeyframe(
                    item.time_seconds,
                    item.x * x_factor,
                    item.y * y_factor,
                )
                for item in self.keyframes
            ),
        )

    def as_dict(self):
        return {
            'crop_width': self.crop_width,
            'crop_height': self.crop_height,
            'keyframes': [
                {'time_seconds': item.time_seconds, 'x': item.x, 'y': item.y}
                for item in self.keyframes
            ],
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            crop_width=int(data['crop_width']),
            crop_height=int(data['crop_height']),
            keyframes=tuple(
                ReframeKeyframe(
                    float(item['time_seconds']),
                    float(item['x']),
                    float(item['y']),
                )
                for item in data.get('keyframes', [])
            ),
        )


class AutoReframeService:
    """Builds a smooth, person-aware crop plan without processing every frame."""

    def __init__(
        self, priority='face', safe_margin=0.15, interval_frames=None, smoothing=0.18,
        top_margin=None, horizontal_smoothing=None, vertical_lock=None,
    ):
        self.priority = priority if priority in {'face', 'body'} else 'face'
        self.safe_margin = min(0.40, max(0.0, float(safe_margin)))
        default_top_margin = 0.18 if self.priority == 'face' else 0.12
        self.top_margin = min(0.35, max(0.0, float(
            default_top_margin if top_margin is None else top_margin,
        )))
        self.interval_frames = max(
            1,
            int(interval_frames or settings.EXTERNAL_MEDIA_AUTO_REFRAME_INTERVAL_FRAMES),
        )
        self.smoothing = min(1.0, max(0.01, float(smoothing)))
        self.horizontal_smoothing = min(1.0, max(0.01, float(
            max(0.36, self.smoothing) if horizontal_smoothing is None else horizontal_smoothing,
        )))
        self.vertical_lock = (self.priority == 'face') if vertical_lock is None else bool(vertical_lock)

    def analyze(self, video_path: Path, output_width: int, output_height: int):
        try:
            import cv2
        except ImportError:
            logger.warning('OpenCV não está disponível; usando enquadramento central em cover.')
            return None

        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            logger.warning('Não foi possível abrir %s para Auto Reframe.', video_path)
            return None
        try:
            source_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            source_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
            if source_width <= 0 or source_height <= 0:
                return None
            crop_width, crop_height = self.cover_crop_size(
                source_width, source_height, output_width, output_height,
            )
            face_detectors = self._face_detectors(cv2)
            body_detector = cv2.HOGDescriptor()
            body_detector.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
            observations = []
            frame_index = 0
            last_box = None
            last_person_count = None
            pending_person_count = None
            pending_count_samples = 0
            missing_samples = 0
            hold_samples = max(6, round((fps / self.interval_frames) * 2))
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                if frame_index % self.interval_frames:
                    frame_index += 1
                    continue
                detection = self._detect_people(frame, face_detectors, body_detector, cv2)
                if detection is None:
                    missing_samples += 1
                    if last_box is not None and missing_samples <= hold_samples:
                        box = last_box
                    else:
                        box = None
                else:
                    candidate_box, person_count = detection
                    missing_samples = 0
                    if last_box is not None and person_count != last_person_count:
                        if pending_person_count != person_count:
                            pending_person_count = person_count
                            pending_count_samples = 1
                        else:
                            pending_count_samples += 1
                        if pending_count_samples < hold_samples:
                            box = last_box
                        else:
                            box = candidate_box
                            last_box = candidate_box
                            last_person_count = person_count
                            pending_person_count = None
                            pending_count_samples = 0
                    else:
                        box = candidate_box
                        last_box = candidate_box
                        last_person_count = person_count
                        pending_person_count = None
                        pending_count_samples = 0
                if box is not None:
                    observations.append((frame_index / max(fps, 0.001), box))
                frame_index += 1
            if not observations:
                logger.info('Nenhuma pessoa detectada em %s; usando crop central.', video_path)
                return None
            crop_width, crop_height = self._smart_crop_size(
                observations, crop_width, crop_height, source_width, source_height,
                output_width / output_height,
            )
            keyframes = self._smooth_keyframes(
                observations, crop_width, crop_height, source_width, source_height,
            )
            return AutoReframePlan(crop_width, crop_height, tuple(keyframes))
        except Exception:
            logger.exception('Falha na análise do Auto Reframe; usando crop central.')
            return None
        finally:
            capture.release()

    @staticmethod
    def cover_crop_size(source_width, source_height, output_width, output_height):
        target_ratio = output_width / output_height
        source_ratio = source_width / source_height
        if source_ratio > target_ratio:
            crop_height = source_height
            crop_width = round(crop_height * target_ratio)
        else:
            crop_width = source_width
            crop_height = round(crop_width / target_ratio)
        return AutoReframeService._even(crop_width), AutoReframeService._even(crop_height)

    def _detect_people(self, frame, face_detectors, body_detector, cv2):
        original_height, original_width = frame.shape[:2]
        max_width = settings.EXTERNAL_MEDIA_AUTO_REFRAME_MAX_ANALYSIS_WIDTH
        scale = min(1.0, max_width / max(1, original_width))
        if scale < 1.0:
            analyzed = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        else:
            analyzed = frame
        boxes = []
        if self.priority == 'body':
            detected, _weights = body_detector.detectMultiScale(
                analyzed, winStride=(8, 8), padding=(8, 8), scale=1.05,
            )
            boxes = [tuple(map(float, box)) for box in detected]
        if not boxes:
            gray = cv2.cvtColor(analyzed, cv2.COLOR_BGR2GRAY)
            faces = self._detect_faces(gray, face_detectors, cv2)
            for x, y, width, height in faces:
                if self.priority == 'face':
                    boxes.append(self._face_priority_box(x, y, width, height))
                else:
                    boxes.append((x - width * 1.2, y - height * 0.7, width * 3.4, height * 4.8))
        if not boxes:
            return None
        inverse_scale = 1.0 / scale
        left = min(box[0] for box in boxes) * inverse_scale
        top = min(box[1] for box in boxes) * inverse_scale
        right = max(box[0] + box[2] for box in boxes) * inverse_scale
        bottom = max(box[1] + box[3] for box in boxes) * inverse_scale
        return (
            (
                max(0.0, left), max(0.0, top),
                min(float(original_width), right), min(float(original_height), bottom),
            ),
            len(boxes),
        )

    @staticmethod
    def _face_detectors(cv2):
        detectors = []
        for filename in (
            'haarcascade_frontalface_default.xml',
            'haarcascade_frontalface_alt2.xml',
        ):
            detector = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / filename))
            if not detector.empty():
                detectors.append(detector)
        return detectors

    @staticmethod
    def _detect_faces(gray, face_detectors, cv2):
        if not face_detectors:
            return []
        min_size = (max(24, gray.shape[1] // 35),) * 2
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        variants = (
            gray,
            clahe.apply(gray),
            cv2.equalizeHist(gray),
        )
        for detector in face_detectors:
            for variant in variants:
                faces = detector.detectMultiScale(
                    variant, scaleFactor=1.08, minNeighbors=4, minSize=min_size,
                )
                if len(faces):
                    return faces
        return []

    def _smart_crop_size(
        self, observations, cover_width, cover_height, source_width, source_height, target_ratio,
    ):
        required_widths = []
        required_heights = []
        margin_factor = 1.0 + (2.0 * self.safe_margin)
        for _time, (left, top, right, bottom) in observations:
            box_width = max(1.0, right - left) * margin_factor
            box_height = max(1.0, bottom - top) * margin_factor
            width = max(box_width, box_height * target_ratio)
            height = width / target_ratio
            required_widths.append(width)
            required_heights.append(height)
        # The 90th percentile ignores a single detector outlier while protecting most movement.
        index = min(len(required_widths) - 1, math.floor(len(required_widths) * 0.90))
        desired_width = sorted(required_widths)[index]
        desired_height = sorted(required_heights)[index]
        # Avoid an excessively tight digital zoom; at least 45% of the cover window remains visible.
        crop_width = min(cover_width, max(cover_width * 0.45, desired_width))
        crop_height = min(cover_height, max(cover_height * 0.45, desired_height))
        if crop_width / crop_height > target_ratio:
            crop_height = crop_width / target_ratio
        else:
            crop_width = crop_height * target_ratio
        crop_width = min(crop_width, source_width)
        crop_height = min(crop_height, source_height)
        return self._even(crop_width), self._even(crop_height)

    def _smooth_keyframes(self, observations, crop_width, crop_height, source_width, source_height):
        keyframes = []
        smooth_x = smooth_y = None
        max_x = max(0.0, source_width - crop_width)
        max_y = max(0.0, source_height - crop_height)
        targets = [
            (
                time_seconds,
                min(max_x, max(0.0, (((left + right) / 2.0) - crop_width / 2.0))),
                self._target_crop_y(top, bottom, crop_height, max_y),
            )
            for time_seconds, (left, top, right, bottom) in observations
        ]
        locked_y = self._locked_vertical_target([target_y for _time, _target_x, target_y in targets], max_y)
        for time_seconds, target_x, target_y in targets:
            if locked_y is not None:
                target_y = locked_y
            if smooth_x is None:
                smooth_x, smooth_y = target_x, target_y
            else:
                smooth_x += self.horizontal_smoothing * (target_x - smooth_x)
                smooth_y += self.smoothing * (target_y - smooth_y)
            keyframes.append(ReframeKeyframe(time_seconds, smooth_x, smooth_y))
        tolerance = max(1.0, min(crop_width, crop_height) * 0.003)
        return self._simplify(keyframes, tolerance)

    def _locked_vertical_target(self, target_ys, max_y):
        if not self.vertical_lock or not target_ys or max_y <= 0:
            return None
        values = sorted(min(max_y, max(0.0, value)) for value in target_ys)
        trim = math.floor(len(values) * 0.20)
        if trim and len(values) > trim * 2:
            values = values[trim:-trim]
        return values[len(values) // 2]

    def _target_crop_y(self, top, bottom, crop_height, max_y):
        ideal_y = top - (crop_height * self.top_margin)
        lowest_y_that_preserves_bottom = bottom + (crop_height * self.safe_margin) - crop_height
        if lowest_y_that_preserves_bottom <= ideal_y:
            target_y = ideal_y
        else:
            # When the visible person is taller than the crop, keep the headroom close to ideal
            # and only move down as much as needed to avoid losing too much body.
            target_y = ideal_y + ((lowest_y_that_preserves_bottom - ideal_y) * 0.10)
        return min(max_y, max(0.0, target_y))

    @staticmethod
    def _face_priority_box(x, y, width, height):
        # Keep the top near the real head while still expanding downward for upper-body context.
        top = y - height * 0.22
        bottom = y + height * 4.5
        return (
            x - width * 1.7,
            top,
            width * 4.4,
            bottom - top,
        )

    @classmethod
    def _simplify(cls, points, tolerance):
        if len(points) <= 2:
            return points
        first, last = points[0], points[-1]
        duration = max(0.001, last.time_seconds - first.time_seconds)
        greatest_distance = 0.0
        greatest_index = 0
        for index, point in enumerate(points[1:-1], start=1):
            ratio = (point.time_seconds - first.time_seconds) / duration
            expected_x = first.x + ((last.x - first.x) * ratio)
            expected_y = first.y + ((last.y - first.y) * ratio)
            distance = math.hypot(point.x - expected_x, point.y - expected_y)
            if distance > greatest_distance:
                greatest_distance = distance
                greatest_index = index
        if greatest_distance <= tolerance:
            return [first, last]
        left = cls._simplify(points[:greatest_index + 1], tolerance)
        right = cls._simplify(points[greatest_index:], tolerance)
        return left[:-1] + right

    @staticmethod
    def _even(value):
        return max(2, int(round(value)) // 2 * 2)
