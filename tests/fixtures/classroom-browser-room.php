<?php
declare(strict_types=1);

// Render the production template with test-only endpoints and synthetic identities.
$plugin = getenv('MPM_TEST_CLASSROOM_ROOT');
require $plugin . '/lesson.php';
$GLOBALS['MTAG_PLUGIN_CONTEXT'] = ['base'=>'/plugins/school-plugins.php?plugin=school-classroom', 'site_base'=>'/', 'csrf'=>'fixture', 'store'=>'fixture'];
$staff = getenv('MPM_TEST_ROOM_ROLE') !== 'student';
$cfg = ['room_video_quality'=>true,'screen_share'=>true,'student_screen_share'=>true];
$actor = ['role'=>$staff ? 'teacher' : 'student'];
$class = ['id'=>22];
$schoolName = 'مدرسهٔ آزمایشی';
$classLabel = '۷/۱';
$pageTitle = $schoolName . ' ' . $classLabel;
$panel = 'school-panel.php';
$bellDate = '2026-11-18';
$choices = [];
?>
<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<link rel="stylesheet" href="<?= cl_html(cl_asset('classroom.css')) ?>">
<script defer src="<?= cl_html(cl_asset('board.js')) ?>"></script>
<script defer src="/fixture.js"></script>
</head><body><main class="school-main room-main">
<?php require $plugin . '/room-view.php'; ?>
</main></body></html>
