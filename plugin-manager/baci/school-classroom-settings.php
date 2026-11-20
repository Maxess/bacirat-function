<?php
// Baci's «تنظیمات دیگر» loads this page in its same-origin management hub.
require_once __DIR__ . '/includes/auth.php';
require_admin();
header('X-Frame-Options: SAMEORIGIN');
$schoolClassroomGateway = __DIR__ . '/plugins/school-plugins.php';
$schoolClassroomForm = 'school-classroom-settings.php' . (($_GET['embedded'] ?? '') === '1' ? '?embedded=1' : '');
if (is_file($schoolClassroomGateway)) {
    require_once $schoolClassroomGateway;
    $schoolClassroomView = mpm_prepare_settings($schoolClassroomForm);
} else {
    http_response_code(404);
    $schoolClassroomView = null;
}
require_once __DIR__ . '/includes/header.php';
?>
<section id="school-plugins-tab" class="card" aria-label="تنظیم و نصب افزونه‌ها">
    <?php if ($schoolClassroomView !== null) mpm_settings_fragment($schoolClassroomView, $schoolClassroomForm);
    else echo '<p role="alert">مدیر افزونه‌ها نصب نیست.</p>'; ?>
</section>
<?php require_once __DIR__ . '/includes/footer.php'; ?>
