<?php
declare(strict_types=1);

const CL_QUALITY_FIELDS = [
    'teacherCamera'=>'دوربین دبیر', 'studentCamera'=>'دوربین دانش‌آموز',
    'teacherScreen'=>'اشتراک صفحهٔ دبیر', 'studentScreen'=>'اشتراک صفحهٔ دانش‌آموز',
];

function cl_video_presets(): array
{
    static $presets;
    return $presets ??= json_decode(file_get_contents(__DIR__ . '/video-presets.json'), true, 16, JSON_THROW_ON_ERROR);
}

function cl_quality_enabled(array $cfg): bool
{
    return ($cfg['room_video_quality'] ?? false) === true;
}

function cl_quality_control(array $cfg, array $lesson, array $actor): bool
{
    return cl_quality_enabled($cfg) && cl_is_staff($actor) && cl_assigned($actor, $lesson['teacher']);
}

function cl_quality_key(array $class): string
{
    // This is a class/school-year preference, not a timetable row or a live-room record.
    return $class['id'] . ':' . $class['year'];
}

function cl_quality_levels(ClassroomStore $s, array $cfg, array $class): array
{
    $levels = array_fill_keys(array_keys(CL_QUALITY_FIELDS), 'low');
    if (!cl_quality_enabled($cfg)) return $levels; // Retain saved choices while the feature is off.
    $saved = $s->get('video-quality', cl_quality_key($class)) ?? [];
    $valid = array_keys(cl_video_presets()['levels']);
    foreach ($levels as $key=>&$level) if (in_array($saved[$key] ?? null, $valid, true)) $level = $saved[$key];
    return $levels;
}

function cl_video_profile(array $cfg, string $kind, string $level): array
{
    $presets = cl_video_presets();
    $type = $presets[$kind];
    [$size, $defaultFps] = $type['defaults'][$level];
    $chosen = $cfg[$kind . '_' . $level . '_size'] ?? null;
    if (in_array($chosen, $type['sizes'], true)) $size = $chosen;
    [$width, $height] = array_map('intval', explode('x', $size));
    $fps = filter_var($cfg[$kind . '_' . $level . '_fps'] ?? null, FILTER_VALIDATE_INT,
        ['options'=>['min_range'=>$presets['fps']['min'],'max_range'=>$presets['fps']['max']]]);
    $frameRate = $fps === false ? $defaultFps : $fps;
    // Encoder caps grow with the chosen dimensions and FPS, with a fixed upper bound per source.
    [$baseRate, $basePixelsPerSecond, $minRate, $maxRate] = $kind === 'camera'
        ? [25, 128*128*8, 12, 350] : [70, 640*360*5, 20, 500];
    $bitrate = 1000 * (int)round(max($minRate, min($maxRate, $baseRate * $width * $height * $frameRate / $basePixelsPerSecond)));
    return ['width'=>$width, 'height'=>$height, 'frameRate'=>$frameRate, 'bitrate'=>$bitrate];
}

function cl_quality_status(ClassroomStore $s, array $cfg, array $class, array $lesson, array $actor, array $input = []): array
{
    $levels = cl_quality_levels($s, $cfg, $class);
    $role = cl_is_staff($actor) ? 'teacher' : 'student';
    $quality = ['camera'=>cl_video_profile($cfg, 'camera', $levels[$role . 'Camera']),
        'screen'=>cl_video_profile($cfg, 'screen', $levels[$role . 'Screen'])];
    if (cl_quality_control($cfg, $lesson, $actor)) $quality['levels'] = $levels;
    $rev = substr(hash('sha256', json_encode($quality, JSON_THROW_ON_ERROR)), 0, 24);
    return ['qualityRev'=>$rev] + (($input['qualityRev'] ?? null) === $rev ? [] : ['quality'=>$quality]);
}

/** The caller holds the class lock; a quality preference does not grant any media source. */
function cl_set_quality(ClassroomStore $s, array $cfg, array $class, array $lesson, array $actor, array $body, int $now): array
{
    if (!cl_quality_control($cfg, $lesson, $actor)) throw new ClassroomError(403, 'تنظیم کیفیت داخل کلاس برای این حساب فعال نیست.');
    cl_check_live($cfg, $lesson, $actor, $body, $now);
    $levels = $body['levels'] ?? null;
    if (!is_array($levels) || count($levels) !== count(CL_QUALITY_FIELDS) || array_diff_key($levels, CL_QUALITY_FIELDS)) {
        throw new ClassroomError(400, 'هر چهار کیفیت کلاس را جداگانه انتخاب کن.');
    }
    $valid = array_keys(cl_video_presets()['levels']);
    foreach ($levels as $value) if (!in_array($value, $valid, true)) throw new ClassroomError(400, 'سطح کیفیت معتبر نیست.');
    $s->put('video-quality', cl_quality_key($class), $levels);
    return cl_quality_status($s, $cfg, $class, $lesson, $actor);
}
