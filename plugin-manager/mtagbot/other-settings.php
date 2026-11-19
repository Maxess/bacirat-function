<?php
// File: other-settings.php – native plugin tab for the reviewed mtagbot host.
require_once __DIR__ . '/includes/auth.php';
require_admin();

$hubTabs = [
    'activity' => ['لاگ فعالیت', 'activity-logs.php?embedded=1', 'btn-outline'],
    'migration' => ['مایگریشن', 'migration-updater.php?embedded=1', 'btn-warning'],
    'backups' => ['پشتیبان‌گیری', 'backups.php?embedded=1', 'btn-success'],
    'api' => ['مستندات API', 'api/docs.php?embedded=1', 'btn-outline'],
];
$hubTab = $_GET['tab'] ?? 'activity';
if (!is_string($hubTab) || (!isset($hubTabs[$hubTab]) && $hubTab !== 'plugins')) $hubTab = 'activity';
$hubGateway = __DIR__ . '/plugins/school-plugins.php';
if (is_file($hubGateway)) require_once $hubGateway;
$hubPluginsVisible = function_exists('mpm_settings_visible') && mpm_settings_visible();
$hubPluginView = null;
if ($hubTab === 'plugins') {
    if (function_exists('mpm_prepare_settings')) $hubPluginView = mpm_prepare_settings(); // POST and redirects before any markup.
    else http_response_code(404);
}
require_once __DIR__ . '/includes/header.php';
?>
<div class="integrated-hub">
    <nav class="card integrated-tabs" aria-label="تنظیمات دیگر">
        <?php foreach ($hubTabs as $id => [$label, $url, $color]): ?>
        <a class="btn <?= $hubTab === $id ? 'btn-primary' : $color ?>" href="other-settings.php?tab=<?= $id ?>" data-hub-url="<?= clean($url) ?>" data-hub-color="<?= $color ?>"<?= $hubTab === $id ? ' aria-current="page"' : '' ?>><?= clean($label) ?></a>
        <?php endforeach; ?>
        <?php if ($hubPluginsVisible): ?>
        <a class="btn <?= $hubTab === 'plugins' ? 'btn-primary' : 'btn-outline' ?>" href="other-settings.php?tab=plugins"<?= $hubTab === 'plugins' ? ' aria-current="page"' : '' ?>>افزونه‌ها</a>
        <?php endif; ?>
    </nav>
    <?php if ($hubTab === 'plugins'): ?>
    <section class="card" aria-label="تنظیم و نصب افزونه‌ها" id="school-plugins-tab">
        <?php if ($hubPluginView !== null) mpm_settings_fragment($hubPluginView);
        else echo '<p role="status">مدیر افزونه‌ها نصب نیست.</p>'; ?>
    </section>
    <?php else: ?>
    <iframe id="hubFrame" title="<?= clean($hubTabs[$hubTab][0]) ?>" src="<?= clean($hubTabs[$hubTab][1]) ?>"></iframe>
    <?php endif; ?>
</div>
<script>
(function () {
    const frame = document.getElementById('hubFrame');
    if (!frame) return;
    const links = document.querySelectorAll('.integrated-tabs [data-hub-url]');
    links.forEach(a => a.addEventListener('click', event => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0) return;
        event.preventDefault();
        frame.src = a.dataset.hubUrl;
        frame.title = a.textContent;
        links.forEach(link => {
            link.classList.remove('btn-primary', link.dataset.hubColor);
            link.classList.add(link === a ? 'btn-primary' : link.dataset.hubColor);
            if (link === a) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
        });
        history.replaceState(null, '', a.href);
    }));
})();
</script>
<?php require_once __DIR__ . '/includes/footer.php'; ?>
