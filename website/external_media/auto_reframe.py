from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings


logger = logging.getLogger(__name__)

# FFmpeg chokes on crop expressions with too many nested `if(lt(t,...))` branches.
# Long clips can still produce 100+ keyframes even after Douglas-Peucker simplify,
# so we cap what actually goes into the filter graph (same idea as spectral ducking).
MAX_FFMPEG_CROP_KEYFRAMES = 48
# Increment whenever the crop strategy changes. Cached proxy plans from older
# strategies must not be reused by a reprocess.
AUTO_REFRAME_PLAN_VERSION = 9


def limit_keyframes_for_ffmpeg(keyframes, max_count=MAX_FFMPEG_CROP_KEYFRAMES):
    """Downsamples keyframes to a safe count while always keeping the first and last."""
    if len(keyframes) <= max_count:
        return keyframes
    if max_count < 2:
        return keyframes[:1]
    last_index = len(keyframes) - 1
    indices = [round(index * last_index / (max_count - 1)) for index in range(max_count)]
    deduped = []
    for index in indices:
        if not deduped or deduped[-1] != index:
            deduped.append(index)
    return [keyframes[index] for index in deduped]


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
        first = keyframes[0]
        first_value = round(getattr(first, axis), 3)
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
        if first.time_seconds > 0:
            return f'if(lt(t\\,{first.time_seconds:.3f})\\,{first_value}\\,{expression})'
        return expression

    def ffmpeg_filters(self, width, height):
        keyframes = limit_keyframes_for_ffmpeg(self.keyframes)
        x_expression = self._expression(keyframes, 'x')
        y_expression = self._expression(keyframes, 'y')
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
        self.priority = priority if priority in {'face', 'body', 'static'} else 'face'
        # A face-only crop feels like a webcam close-up and is very unforgiving if
        # detection misses a strand of hair. Keep enough room for the upper torso.
        requested_safe_margin = min(0.40, max(0.0, float(safe_margin)))
        self.safe_margin = max(0.18 if self.priority == 'face' else 0.15, requested_safe_margin)
        # Face framing should keep the hair/head close to the top edge, without
        # becoming a tight headshot. Older templates stored 12–18%, which made
        # the speaker look noticeably low in wide renders.
        default_top_margin = 0.02 if self.priority == 'face' else 0.10
        requested_top_margin = min(0.35, max(0.0, float(
            default_top_margin if top_margin is None else top_margin,
        )))
        if self.priority == 'face':
            # Keep backward-compatible template settings from reintroducing the
            # excessive headroom. Face priority deliberately operates in a
            # narrow, safe interval: close to the top but never flush against it.
            self.top_margin = min(0.025, max(0.015, requested_top_margin))
        else:
            self.top_margin = max(0.10, requested_top_margin)
        self.interval_frames = max(
            1,
            int(interval_frames or settings.EXTERNAL_MEDIA_AUTO_REFRAME_INTERVAL_FRAMES),
        )
        self.smoothing = min(1.0, max(0.01, float(smoothing)))
        self.horizontal_smoothing = min(1.0, max(0.01, float(
            (0.48 if self.priority == 'face' else max(0.15, self.smoothing))
            if horizontal_smoothing is None else horizontal_smoothing,
        )))
        # Ignore short lateral gestures, but follow a speaker who remains outside
        # the center for successive samples.
        self.horizontal_deadzone_ratio = 0.025
        self.horizontal_persistence_samples = 2
        # Primeiros ~1.2s: suavização horizontal um pouco maior só para encontrar a pessoa,
        # sem "colar" no rosto/gestos como o fast-start agressivo anterior.
        self.horizontal_fast_start_seconds = 1.2
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
            if self.priority == 'static':
                return self._static_center_plan(
                    crop_width, crop_height, source_width, source_height,
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

    @classmethod
    def _static_center_plan(cls, cover_width, cover_height, source_width, source_height):
        """Apply a stable six-percent podcast crop without person tracking."""
        zoom = 1.06
        crop_width = cls._even(min(source_width, cover_width / zoom))
        crop_height = cls._even(min(source_height, cover_height / zoom))
        x = max(0.0, (source_width - crop_width) / 2.0)
        y = max(0.0, (source_height - crop_height) / 2.0)
        return AutoReframePlan(
            crop_width,
            crop_height,
            (ReframeKeyframe(0.0, x, y),),
        )

    def _detect_people(self, frame, face_detectors, body_detector, cv2):
        original_height, original_width = frame.shape[:2]
        max_width = settings.EXTERNAL_MEDIA_AUTO_REFRAME_MAX_ANALYSIS_WIDTH
        scale = min(1.0, max_width / max(1, original_width))
        if scale < 1.0:
            analyzed = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        else:
            analyzed = frame
        body_boxes = self._detect_bodies(analyzed, body_detector, cv2)
        # HOG body detections frequently begin at the shoulders, which is precisely
        # what caused heads/hair to be cropped. When a face is available it is the
        # most reliable top anchor, while the expanded box keeps head + upper torso.
        gray = cv2.cvtColor(analyzed, cv2.COLOR_BGR2GRAY)
        faces = self._detect_faces(gray, face_detectors, cv2)
        face_priority_detection = bool(faces and self.priority == 'face')
        if face_priority_detection:
            boxes = [self._face_priority_box(x, y, width, height) for x, y, width, height in faces]
        elif body_boxes:
            boxes = body_boxes
        else:
            boxes = [
                (x - width * 1.2, y - height * 0.7, width * 3.4, height * 4.8)
                for x, y, width, height in faces
            ]
        if not boxes:
            return None
        inverse_scale = 1.0 / scale
        left = min(box[0] for box in boxes) * inverse_scale
        top = min(box[1] for box in boxes) * inverse_scale
        right = max(box[0] + box[2] for box in boxes) * inverse_scale
        bottom = max(box[1] + box[3] for box in boxes) * inverse_scale
        if not face_priority_detection:
            left, top, right, bottom = self._normalize_detection_box(
                left, top, right, bottom, original_width, original_height,
            )
        return (
            (
                max(0.0, left), max(0.0, top),
                min(float(original_width), right), min(float(original_height), bottom),
            ),
            len(boxes),
        )

    def _detect_bodies(self, analyzed, body_detector, cv2):
        gray = cv2.cvtColor(analyzed, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        candidates = (
            analyzed,
            cv2.cvtColor(clahe.apply(gray), cv2.COLOR_GRAY2BGR),
        )
        boxes = []
        for candidate in candidates:
            detected, _weights = body_detector.detectMultiScale(
                candidate,
                winStride=(8, 8),
                padding=(16, 16),
                scale=1.04,
                hitThreshold=0,
            )
            if len(detected):
                boxes = [tuple(map(float, box)) for box in detected]
                break
        return boxes

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
        variant_specs = [
            (gray, 1.0),
            (clahe.apply(gray), 1.0),
            (cv2.equalizeHist(gray), 1.0),
            (cv2.bilateralFilter(gray, 5, 50, 50), 1.0),
        ]
        if gray.shape[1] < 960:
            upscaled = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
            variant_specs.extend([
                (upscaled, 1.5),
                (clahe.apply(upscaled), 1.5),
            ])
        for detector in face_detectors:
            for variant, scale_factor in variant_specs:
                for min_neighbors in (4, 3):
                    faces = detector.detectMultiScale(
                        variant,
                        scaleFactor=1.05,
                        minNeighbors=min_neighbors,
                        minSize=min_size,
                    )
                    if len(faces):
                        inverse_scale = 1.0 / scale_factor
                        return [
                            (
                                face[0] * inverse_scale,
                                face[1] * inverse_scale,
                                face[2] * inverse_scale,
                                face[3] * inverse_scale,
                            )
                            for face in faces
                        ]
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
        # NOTE: on purpose, this never zooms in further just to gain horizontal pan room to
        # center an off-center person (a "centering zoom" was tried before and reverted): since
        # width/height are locked to `target_ratio`, any extra zoom tight enough to fully center
        # a person in a wide/short ratio (e.g. 16:5 banners) also shrinks the vertical framing by
        # the same factor — which crops below the head/chin on the common case of a close/medium
        # shot. Keeping the person fully framed takes priority over perfect centering.
        # Face framing starts exactly at the cover crop: this is the least zoom
        # possible while still filling the output canvas. The body profile retains
        # its small adaptive zoom, which is useful for vertical social formats.
        if self.priority == 'face':
            # A 16:5 cover crop normally uses the entire source width, leaving no
            # horizontal room for the tracker. Only when the speaker consistently
            # stands away from center reserve up to 4% for a subtle pan.
            tracking_zoom = 0.96 if self._needs_tracking_pan(observations, source_width) else 1.0
            crop_width = cover_width * tracking_zoom
            crop_height = cover_height * tracking_zoom
        else:
            crop_width = min(cover_width, max(cover_width * 0.99, desired_width))
            crop_height = min(cover_height, max(cover_height * 0.99, desired_height))
        if crop_width / crop_height > target_ratio:
            crop_height = crop_width / target_ratio
        else:
            crop_width = crop_height * target_ratio
        crop_width = min(crop_width, source_width)
        crop_height = min(crop_height, source_height)
        return self._even(crop_width), self._even(crop_height)

    @staticmethod
    def _needs_tracking_pan(observations, source_width):
        if len(observations) < 2 or source_width <= 0:
            return False
        anchors = sorted(
            AutoReframeService._horizontal_anchor_x(left, top, right, bottom)
            for _time, (left, top, right, bottom) in observations
        )
        median_anchor = anchors[len(anchors) // 2]
        # Ignore natural small variation around center; reserve pan only for a
        # speaker who occupies one side through most of the clip.
        return abs(median_anchor - (source_width / 2.0)) > source_width * 0.08

    def _smooth_keyframes(self, observations, crop_width, crop_height, source_width, source_height):
        keyframes = []
        smooth_x = smooth_y = None
        horizontal_direction = 0
        horizontal_streak = 0
        max_x = max(0.0, source_width - crop_width)
        max_y = max(0.0, source_height - crop_height)
        targets = [
            (
                time_seconds,
                min(max_x, max(0.0, self._horizontal_anchor_x(left, top, right, bottom) - crop_width / 2.0)),
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
                offset = target_x - smooth_x
                deadzone = crop_width * self.horizontal_deadzone_ratio
                direction = 1 if offset > deadzone else (-1 if offset < -deadzone else 0)
                if direction and direction == horizontal_direction:
                    horizontal_streak += 1
                elif direction:
                    horizontal_direction = direction
                    horizontal_streak = 1
                else:
                    horizontal_direction = 0
                    horizontal_streak = 0
                # A brief lean or gesture receives only a modest correction.
                # Two consecutive off-center samples activate the tracker so a
                # speaker who actually moved remains centered.
                h_smooth = self._horizontal_smoothing_at(time_seconds)
                if horizontal_streak < self.horizontal_persistence_samples:
                    h_smooth = min(0.20, h_smooth)
                smooth_x += h_smooth * (target_x - smooth_x)
                smooth_y += self.smoothing * (target_y - smooth_y)
            keyframes.append(ReframeKeyframe(time_seconds, smooth_x, smooth_y))
        tolerance = max(1.0, min(crop_width, crop_height) * 0.003)
        simplified = self._simplify(keyframes, tolerance)
        if simplified and simplified[0].time_seconds > 0:
            simplified = [ReframeKeyframe(0.0, simplified[0].x, simplified[0].y), *simplified]
        return simplified

    def _horizontal_smoothing_at(self, time_seconds):
        fast_start = max(0.25, float(self.horizontal_fast_start_seconds))
        if time_seconds >= fast_start:
            return self.horizontal_smoothing
        ramp = 1.0 - (time_seconds / fast_start)
        boosted = min(0.42, self.horizontal_smoothing * 2.4)
        return min(
            boosted,
            self.horizontal_smoothing + ((boosted - self.horizontal_smoothing) * ramp),
        )

    @staticmethod
    def _horizontal_anchor_x(left, top, right, bottom):
        """Center on the torso, ignoring arm extensions and face jitter."""
        width = max(1.0, right - left)
        height = max(1.0, bottom - top)
        core_left = left + width * 0.25
        core_right = right - width * 0.25
        return (core_left + core_right) / 2.0

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
            # Person taller than the crop: keep the head at the configured headroom and
            # sacrifice lower body instead of sliding the crop down (which cuts the head).
            target_y = ideal_y
        return min(max_y, max(0.0, target_y))

    @staticmethod
    def _normalize_detection_box(left, top, right, bottom, source_width, source_height):
        """Turn raw detector output into a stable interview-style MCU framing box.

        HOG body boxes often start below the hairline; face fallbacks can be tight.
        We pad upward for the head and enforce a minimum height so sizing/positioning
        doesn't over-zoom on the face or drift with inconsistent partial detections.
        """
        box_height = max(1.0, bottom - top)
        head_pad = box_height * 0.30
        top = max(0.0, top - head_pad)
        box_height = max(1.0, bottom - top)

        min_height = source_height * 0.58
        if box_height < min_height:
            center_y = (top + bottom) / 2.0
            half = min_height / 2.0
            top = max(0.0, center_y - half)
            bottom = min(float(source_height), center_y + half)
            if bottom - top < min_height:
                bottom = min(float(source_height), top + min_height)

        return left, top, right, bottom

    @staticmethod
    def _face_priority_box(x, y, width, height):
        # Haar faces begin around the forehead, not the hairline. Add a small upward
        # allowance for hair, but do not run this box through the body normalizer:
        # that previous second expansion pulled the crop to y=0 and created huge
        # empty headroom in wide formats.
        # The cascade starts around the forehead. Estimate enough area above it
        # to cover the full hairline, while the crop top-margin keeps this safety
        # allowance visually tight instead of creating empty headroom.
        # Curly/voluminous hair can extend noticeably above the forehead reported
        # by Haar. This small additional allowance is enough to avoid clipping it
        # while remaining visually close to the top edge.
        top = y - height * 0.30
        bottom = y + height * 4.2
        return (
            x - width * 1.7,
            top,
            width * 4.4,
            bottom - top,
        )

    @staticmethod
    def _stable_body_box_from_face(x, y, width, height):
        """Estimate a body box with a fixed width centered on the face."""
        face_center_x = x + (width / 2.0)
        body_width = width * 2.8
        top = y - height * 0.12
        bottom = y + height * 4.2
        body_height = bottom - top
        return (
            face_center_x - (body_width / 2.0),
            top,
            body_width,
            body_height,
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
