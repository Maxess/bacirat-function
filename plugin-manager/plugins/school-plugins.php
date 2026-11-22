<?php
declare(strict_types=1);

use BaciClassroom\Calendar;

// School plugin runtime and gateway. Settings are exposed by the reviewed host overlay.
const MPM_VERSION = '0.1.24';
const MPM_CLASSROOM_NATIVE = '0.4.0';
const MPM_CLASSROOM_PUPIL_TRIAL = '0.4.3';
const MPM_REGISTRY = 'mtg_plugin_registry';
const MPM_SITE = 'mtg_plugin_site';
const MPM_MAX_ZIP = 4 * 1024 * 1024;
const MPM_MAX_FILES = 32; // Reviewed payload files, excluding plugin.json.
const MPM_BELL_LABELS = [1=>'۱', 2=>'۲', 3=>'۳', 4=>'۴'];
const MPM_CATALOG = [
    'school-classroom' => [
        '0.2.0' => '67f080645d4dce5b709183556b0dd7297c0784388942b2dd00c6157d8a5a117b',
        '0.2.1' => '0cb2f12254559d3b92c925abb3e9244328a3caf9c3386325c8f7d11b0c8c7ea6',
        '0.2.2' => 'ac0eff553b77293722b3c594dac867c7ed1135b16e3c27d5a776a6d013f75931',
        '0.2.3' => 'a9f39ab9a6b1aeb062973f2489b750f3245443b099ffff77915f555dfa92295b',
        '0.3.0' => '159551bb9bba129168c6b3e3f5d4da1468e1f908e7cacb9d3f62e140fdefcb03',
        '0.4.0' => '2b355724e133cb6f10c9312673863f5e2856a5fad53a5d5214a68632106876ac',
        '0.4.1' => '878c80c685ca427514a555b0c65a23fb524e4d232d0f9a9a3e2a652bb145a0c0',
        '0.4.2' => 'b3e0d73b6218752eb19c97a9a28fd36ee75792f4ea1ea72a44311cfc7d7e5b5c',
        '0.4.3' => '7a6c7288400c9cd5f9ba563e2b845564785d689a181021b309455eafa52199e3',
        '0.4.4' => 'b4e8cf0a9a1887b00af5a4685b1b150c1ba8ab1c33704d0c593f2b8b5b31ca6a',
        '0.4.5' => '549980ae530938ce5d1544dbe790e5e26f9d965c53b5d04c30b0716d358fb96a',
        '0.4.6' => '02ccccc105d26560d3e237a3314b635a6d311fc97287ebc7305dbc691a2a1a80',
        '0.4.7' => 'd4f639e75dd6d396a17fe46914b4d91da2a3dc8c54da12fcabae3bf4fcb86f94',
        '0.4.8' => '495ea3133cbbc74f5f51c90133c67f5c38ab74f1a328493bd52406ca2c68ff34',
        '0.4.9' => 'f44e2910c0cdbc5bce1a599980d594fe8271e7d5713ffed20d9352f04732bc5d',
        '0.4.10' => 'dec342dc941221a036554959c688cef2e2fb4921c4527447d737664b7820117e',
        '0.4.11' => '88d32ab3f56ea0c52e59c9cbfb41e178bfb5bcf9e5ae1ae88433be6997202964',
        '0.4.12' => 'fbfdc731b25c38dff963f5d98069807aaef117902824011272abae3a781d9369',
        '0.4.13' => '8a4746726dda14bbb243de3cb55310da46dfeb14625eee4a27b961beb6e0bf0b',
        '0.4.14' => 'f0a14a694a35f53b2b739ca71134bf49130e72d76527b3a8ffa5204cd0ace5f3',
        '0.4.15' => '8f346d7b8ecd64f67fe0eb8b1cc6ab7e984a11af87252dfc6e9f573edfbca8f2',
        '0.4.16' => '4a3b2c1bb9c2dbaf2765bcef58ece33e4811abc268dfc96f994faa5ec1e9a84f',
        '0.4.17' => '0a9544c8d8a0e35f451b858d9f634c3b3abf99df16f620dc1111f6ec5b97f325',
        '0.4.18' => '192a8c759dde6ecf1c51dec7b9b33f6a60cd2fd518fc0dc9b6c6ab9d809d48de',
    ],
];

final class MpmNotice extends RuntimeException {}

function mpm_html(string $s): string
{
    return htmlspecialchars($s, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function mpm_query(PDO $db, string $sql, array $args = []): PDOStatement
{
    $stmt = $db->prepare($sql);
    $stmt->execute($args);
    return $stmt;
}

function mpm_host_root(): string
{
    return dirname(__DIR__);
}

function mpm_database(): PDO
{
    require_once mpm_host_root() . '/includes/db.php';
    $pdo = DB::getInstance()->getPdo();
    if (!$pdo instanceof PDO || $pdo->getAttribute(PDO::ATTR_DRIVER_NAME) !== 'mysql') {
        throw new MpmNotice('اتصال MySQL اصلی همین سایت در دسترس نیست. روی پایگاه جایگزین نصب انجام نمی‌شود.');
    }
    return $pdo;
}

function mpm_https_origin(): ?string
{
    $host = strtolower((string)($_SERVER['HTTP_HOST'] ?? ''));
    if (!in_array(strtolower((string)($_SERVER['HTTPS'] ?? '')), ['on', '1'], true) || !mpm_hostname($host)) return null;
    return 'https://' . $host;
}

function mpm_origin(): string
{
    if ($origin = mpm_https_origin()) return $origin;
    http_response_code(403);
    throw new MpmNotice('افزونه فقط روی دامنهٔ HTTPS سایت آزمایشی فعال می‌شود.');
}

function mpm_site_path(): string
{
    if (PHP_SAPI === 'cli') $path = $GLOBALS['MPM_CLI_SITE_PATH'] ?? '';
    else {
        $script = (string)($_SERVER['SCRIPT_NAME'] ?? '');
        $path = preg_replace('~/(?:other-settings\.php|school-classroom-settings\.php|plugins/school-plugins\.php)$~D', '', $script);
        if ($path === $script) throw new MpmNotice('مسیر عمومی افزونه شناخته نشد.');
    }
    if (strlen($path) > 220 || !preg_match('~^(?:/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63})*$~D', $path)) {
        throw new MpmNotice('زیرمسیر سایت آزمایشی معتبر نیست.');
    }
    return $path;
}

function mpm_site_base(string $origin): string
{
    return $origin . mpm_site_path();
}

function mpm_hostname(string $host): bool
{
    $label = '[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?';
    return strlen($host) <= 253 && preg_match('~^(?:' . $label . '\.)+' . $label . '$~D', $host) === 1
        && filter_var($host, FILTER_VALIDATE_IP) === false;
}

function mpm_table_exists(PDO $db, string $table): bool
{
    if ($db->getAttribute(PDO::ATTR_DRIVER_NAME) === 'sqlite') { // Lifecycle tests only.
        return (bool)mpm_query($db, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", [$table])->fetchColumn();
    }
    return (bool)mpm_query($db, 'SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name=?', [$table])->fetchColumn();
}

function mpm_site(PDO $db): ?array
{
    try {
        $row = mpm_query($db, 'SELECT origin, private_dir FROM ' . MPM_SITE . ' WHERE id=1')->fetch(PDO::FETCH_ASSOC);
        return $row ?: null;
    } catch (PDOException $error) {
        if ($error->getCode() === '42S02' || !mpm_table_exists($db, MPM_SITE)) {
            return null;
        }
        throw $error;
    }
}

function mpm_under(string $path, string $dir): bool
{
    return $path === $dir || str_starts_with($path, rtrim($dir, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR);
}

function mpm_web_boundary(string $root): string
{
    $boundary = mpm_document_root();
    for ($p = $root; $p !== dirname($p); $p = dirname($p)) {
        if (in_array(strtolower(basename($p)), ['public_html', 'www', 'htdocs', 'httpdocs'], true) && mpm_under($boundary, $p)) {
            $boundary = $p; // Also exclude the public parent of a nested test site.
        }
    }
    return $boundary;
}

function mpm_storage(string $origin): string
{
    $root = mpm_host_root();
    $boundary = mpm_web_boundary($root);
    $homeValue = getenv('HOME');
    $home = $homeValue ? realpath($homeValue) : false;
    $parent = $home && $home !== '/' && mpm_under(dirname($boundary), $home) ? $home : dirname($boundary);
    if (!is_writable($parent) || mpm_under($parent, $boundary) || mpm_under($parent, $root)) {
        throw new MpmNotice('PHP باید بتواند یک پوشهٔ خصوصی خارج از ریشهٔ عمومی وب ایجاد کند؛ محدودیت هاست را بررسی کن.');
    }
    // Hash the school root, not the gateway's new plugins/ directory.
    return $parent . '/.mtagbot-plugins-' . substr(hash('sha256', $root . '|' . $origin), 0, 24);
}

function mpm_document_root(): string
{
    $documentRoot = $_SERVER['DOCUMENT_ROOT'] ?? '';
    $path = is_string($documentRoot) && $documentRoot !== '' ? realpath($documentRoot) : false;
    if (!$path || !is_dir($path) || $path === '/') throw new MpmNotice('مسیر ریشهٔ عمومی وب روی هاست معتبر نیست.');
    return $path;
}

function mpm_private_dir(string $path): string
{
    $real = @realpath($path);
    $root = mpm_host_root();
    $boundary = mpm_web_boundary($root);
    if (!$real || $real !== $path || !is_dir($real) || mpm_under($real, $boundary)
        || mpm_under($real, $root) || mpm_under($boundary, $real)) {
        throw new MpmNotice('پوشهٔ خصوصی مدیر افزونه‌ها معتبر یا در دسترس نیست.');
    }
    return $real;
}

function mpm_initialize(PDO $db, string $origin, string $dir): array
{
    if (!is_dir($dir) && !@mkdir($dir, 0700)) {
        throw new MpmNotice('ساخت پوشهٔ خصوصی در هاست ممکن نشد.');
    }
    $dir = mpm_private_dir($dir);
    if (!is_file($dir . '/.htaccess')) {
        mpm_new_file($dir . '/.htaccess', "Require all denied\n");
    }
    return mpm_locked($dir, true, static function () use ($db, $origin, $dir): array {
        $db->exec('CREATE TABLE IF NOT EXISTS ' . MPM_SITE . ' (id INT PRIMARY KEY, origin VARCHAR(260) NOT NULL, private_dir TEXT NOT NULL)');
        $db->exec('CREATE TABLE IF NOT EXISTS ' . MPM_REGISTRY . ' (
            slug VARCHAR(40) PRIMARY KEY, version VARCHAR(32) NOT NULL, manifest_sha CHAR(64) NOT NULL,
            owner CHAR(32) NOT NULL, state VARCHAR(12) NOT NULL, installed_at VARCHAR(30) NOT NULL
        )');
        $prior = mpm_site($db);
        if ($prior && ($prior['origin'] !== $origin || $prior['private_dir'] !== $dir)) {
            throw new MpmNotice('مدیر قبلاً به یک دامنه یا مسیر دیگر متصل شده است؛ پایگاه تست را بررسی کن.');
        }
        if (!$prior) {
            if (!is_file($dir . '/settings.key')) {
                mpm_new_file($dir . '/settings.key', random_bytes(32));
            }
            mpm_query($db, 'INSERT INTO ' . MPM_SITE . ' (id,origin,private_dir) VALUES (1,?,?)', [$origin, $dir]);
        }
        return ['origin' => $origin, 'private_dir' => $dir];
    });
}

function mpm_locked(string $dir, bool $exclusive, callable $work): mixed
{
    $file = @fopen($dir . '/manager.lock', 'c');
    if (!$file) throw new MpmNotice('قفل مدیریت افزونه‌ها در دسترس نیست.');
    $mode = ($exclusive ? LOCK_EX : LOCK_SH) | LOCK_NB;
    $until = microtime(true) + 4;
    while (!flock($file, $mode)) {
        if (microtime(true) >= $until) {
            fclose($file);
            throw new MpmNotice('عملیات دیگری روی افزونه‌ها در حال انجام است؛ چند ثانیه بعد دوباره امتحان کن.');
        }
        usleep(50000);
    }
    try {
        return $work();
    } finally {
        flock($file, LOCK_UN);
        fclose($file);
    }
}

function mpm_new_file(string $path, string $bytes): void
{
    $fp = @fopen($path, 'xb');
    if (!$fp) throw new MpmNotice('نوشتن فایل در پوشهٔ خصوصی ممکن نشد.');
    @chmod($path, 0600);
    $size = fwrite($fp, $bytes);
    fclose($fp);
    if ($size !== strlen($bytes)) {
        @unlink($path);
        throw new MpmNotice('نوشتن فایل ناقص ماند؛ فضای دیسک یا دسترسی هاست را بررسی کن.');
    }
}

function mpm_remove_tree(string $path): void
{
    if (!file_exists($path) && !is_link($path)) return;
    if (is_link($path) || !is_dir($path)) {
        if (!@unlink($path)) throw new MpmNotice('حذف فایل ممکن نشد؛ پس از بررسی دسترسی دوباره حذف را بزن.');
        return;
    }
    $items = @scandir($path);
    if ($items === false) throw new MpmNotice('خواندن پوشهٔ افزونه برای حذف ممکن نشد.');
    foreach ($items as $item) {
        if ($item !== '.' && $item !== '..') mpm_remove_tree($path . '/' . $item);
    }
    if (!@rmdir($path)) throw new MpmNotice('حذف پوشه ممکن نشد؛ پس از بررسی دسترسی دوباره حذف را بزن.');
}

function mpm_slug(string $slug): string
{
    if (!preg_match('/^[a-z][a-z0-9-]{2,39}$/D', $slug)) throw new MpmNotice('شناسهٔ افزونه معتبر نیست.');
    return $slug;
}

function mpm_data_table(string $slug): string
{
    return 'mtg_plug_' . str_replace('-', '_', mpm_slug($slug)) . '_data';
}

function mpm_has_directory(string $path): bool
{
    if (is_link($path) || (file_exists($path) && (!is_dir($path) || realpath($path) !== $path))) {
        throw new MpmNotice('مسیر پوشهٔ افزونه معتبر نیست؛ فایلی جابه‌جا یا حذف نشد.');
    }
    return is_dir($path);
}

function mpm_plugin_directory(string $dir, string $slug, bool $create = false): string
{
    foreach (['plugins', mpm_slug($slug)] as $component) {
        $dir .= '/' . $component;
        if (!mpm_has_directory($dir) && $create && !@mkdir($dir, 0700)) {
            throw new MpmNotice('ساخت پوشهٔ اختصاصی افزونه ممکن نشد.');
        }
    }
    return $dir;
}

function mpm_paths(string $dir, array $row): array
{
    $owner = (string)($row['owner'] ?? '');
    if (!preg_match('/^[0-9a-f]{32}$/D', $owner)) throw new MpmNotice('مالک فایل‌های افزونه معتبر نیست.');
    $slug = mpm_slug((string)$row['slug']);
    return [mpm_plugin_directory($dir, $slug) . '/' . $owner, $dir . '/' . $slug . '-' . $owner];
}

function mpm_path(string $dir, array $row): string
{
    [$current, $legacy] = mpm_paths($dir, $row);
    $newExists = mpm_has_directory($current);
    $oldExists = mpm_has_directory($legacy);
    if ($newExists && $oldExists) throw new MpmNotice('دو پوشه برای همین نصب پیدا شد؛ هیچ‌کدام جابه‌جا یا حذف نشد.');
    return $oldExists ? $legacy : $current;
}

// The caller holds the manager lock and has verified a disabled, owned installation.
function mpm_relocate(string $dir, array $row): string
{
    $from = mpm_path($dir, $row);
    [$to] = mpm_paths($dir, $row);
    if ($from !== $to) {
        mpm_plugin_directory($dir, $row['slug'], true);
        if (mpm_has_directory($to) || !@rename($from, $to)) {
            throw new MpmNotice('انتقال پوشهٔ قبلی ممکن نشد؛ فایل‌ها و تنظیمات قبلی حفظ شدند.');
        }
        clearstatcache(true);
    }
    return $to;
}

function mpm_row(PDO $db, string $slug): ?array
{
    $row = mpm_query($db, 'SELECT * FROM ' . MPM_REGISTRY . ' WHERE slug=?', [mpm_slug($slug)])->fetch(PDO::FETCH_ASSOC);
    return $row ?: null;
}

function mpm_manifest(string $raw, array $catalog = MPM_CATALOG): array
{
    $manifest = json_decode($raw, true);
    $slug = $manifest['id'] ?? null;
    $version = $manifest['version'] ?? null;
    if (!is_string($slug) || !is_string($version) || !isset($catalog[$slug][$version])
        || !hash_equals($catalog[$slug][$version], hash('sha256', $raw))) {
        throw new MpmNotice('این ZIP در فهرست نسخه‌های تأییدشده نیست یا تغییر کرده است.');
    }
    mpm_slug($slug);
    if (($manifest['format'] ?? null) !== 1 || !is_array($manifest['files'] ?? null) || count($manifest['files']) > MPM_MAX_FILES) {
        throw new MpmNotice('قالب بسته پشتیبانی نمی‌شود.');
    }
    foreach ($manifest['files'] as $path => $sha) {
        if (!is_string($path) || !preg_match('~^(?:assets/)?[a-zA-Z0-9_-][a-zA-Z0-9_.-]{0,95}$~D', $path)
            || $path === 'plugin.json' || !is_string($sha) || !preg_match('/^[a-f0-9]{64}$/D', $sha)) {
            throw new MpmNotice('مسیر یا هش فایل بسته معتبر نیست.');
        }
    }
    return $manifest;
}

function mpm_read_package(string $path): array
{
    if (!class_exists('ZipArchive')) throw new MpmNotice('افزونهٔ PHP Zip (ZipArchive) روی هاست فعال نیست.');
    if (!is_file($path) || filesize($path) > MPM_MAX_ZIP) throw new MpmNotice('حجم ZIP نباید بیشتر از ۴ مگابایت باشد.');
    $zip = new ZipArchive();
    if ($zip->open($path, ZipArchive::RDONLY) !== true) throw new MpmNotice('فایل ZIP قابل خواندن نیست.');
    try {
        $stat = $zip->statName('plugin.json');
        if ($zip->numFiles > MPM_MAX_FILES + 1 || !$stat || $stat['size'] > 16384) throw new MpmNotice('این ZIP بستهٔ قابل نصب مدیر افزونه‌ها نیست.');
        $raw = $zip->getFromName('plugin.json', 16385);
        if ($raw === false) throw new MpmNotice('فهرست بسته خوانده نشد.');
        $manifest = mpm_manifest($raw);
        $seen = [];
        $total = 0;
        $files = [];
        for ($i = 0; $i < $zip->numFiles; $i++) {
            $entry = $zip->statIndex($i);
            $name = $entry['name'] ?? '';
            if (isset($seen[$name]) || ($name !== 'plugin.json' && !isset($manifest['files'][$name]))) {
                throw new MpmNotice('ZIP دارای فایل اضافه، تکراری یا مسیر نامعتبر است.');
            }
            $seen[$name] = true;
            if ($name === 'plugin.json') continue;
            $size = (int)$entry['size'];
            $total += $size;
            if ($size < 0 || $size > 4 * 1024 * 1024 || $total > 8 * 1024 * 1024) throw new MpmNotice('اندازهٔ بازشدهٔ بسته بیش از حد مجاز است.');
            $data = $zip->getFromIndex($i, $size + 1);
            if ($data === false || strlen($data) !== $size || !hash_equals($manifest['files'][$name], hash('sha256', $data))) {
                throw new MpmNotice('محتوای فایل با بستهٔ تأییدشده تفاوت دارد.');
            }
            $files[$name] = $data;
        }
        if (count($files) !== count($manifest['files'])) throw new MpmNotice('بسته کامل نیست.');
        return [$manifest, $raw, $files];
    } finally {
        $zip->close();
    }
}

function mpm_write_package(string $path, string $raw, array $files): void
{
    if (!@mkdir($path, 0700)) throw new MpmNotice('ساخت پوشهٔ افزونه ممکن نشد.');
    mpm_new_file($path . '/plugin.json', $raw);
    if (!@mkdir($path . '/assets', 0700)) throw new MpmNotice('ساخت پوشهٔ فایل‌های افزونه ممکن نشد.');
    foreach ($files as $name => $data) mpm_new_file($path . '/' . $name, $data);
}

function mpm_release_dir(string $dir, array $row): string
{
    $base = mpm_path($dir, $row);
    $sha = $row['manifest_sha'];
    if (!preg_match('/^[0-9a-f]{64}$/D', $sha) || !is_dir($base) || is_link($base)) {
        throw new MpmNotice('مسیر نسخهٔ افزونه معتبر نیست.');
    }
    $release = $base . '/release-' . $sha;
    if (is_link($release) || (file_exists($release) && !is_dir($release))) {
        throw new MpmNotice('مسیر نسخهٔ افزونه معتبر نیست.');
    }
    return is_dir($release) ? $release : $base; // Installations from older managers have a flat layout.
}

function mpm_upgrade(PDO $db, string $dir, array $row, array $manifest, string $raw, array $files): void
{
    if (version_compare($manifest['version'], $row['version'], '<=')) {
        throw new MpmNotice('برای به‌روزرسانی، ZIP نسخهٔ جدیدتر را انتخاب کن.');
    }
    if ($row['state'] !== 'disabled' || mpm_owned_row($db, $row)['runtime'] !== null) {
        throw new MpmNotice('برای به‌روزرسانی ابتدا اتاق را ببند و افزونه را غیرفعال کن.');
    }
    $previous = mpm_manifest_for($dir, $row);
    if ($manifest['format'] !== $previous['format'] || ($manifest['settings'] !== $previous['settings']
        && !in_array($previous['version'], $manifest['preserve_settings_from'] ?? [], true))) {
        throw new MpmNotice('ساختار تنظیمات نسخهٔ جدید با نصب فعلی سازگار نیست؛ تنظیمات تغییر نکرد.');
    }
    $sha = hash('sha256', $raw);
    $staged = mpm_relocate($dir, $row) . '/release-' . $sha;
    // A retry may find an interrupted write of this exact reviewed release; never replace active files.
    mpm_remove_tree($staged);
    try {
        mpm_write_package($staged, $raw, $files);
        if (!empty($manifest['store'])) mpm_prepare_store($db, $row);
    } catch (Throwable $error) {
        mpm_remove_tree($staged);
        throw $error;
    }
    // Switch only after data migration and verification. Owner and existing attendance remain the same.
    $db->beginTransaction();
    try {
        if (mpm_native_release(['slug'=>$row['slug'],'version'=>$manifest['version']]) && !mpm_native_release($row)) {
            $values = mpm_settings($db, $dir, $row);
            if ($values !== []) {
                // Old bot preferences (especially the private Rubika pairing) are never transferred into a real school bot.
                $keep = array_intersect_key($values, array_flip(['signal_url','api_key','api_secret','max_participants']));
                if ($keep !== $values) mpm_write_settings($db, $dir, $row, $keep);
            }
            $store = mpm_check_store($db, $row);
            mpm_query($db, "DELETE FROM `$store` WHERE bucket IN ('notify-pending','outbox','rubika-pair','rubika-code','rubika-link','rubika-offset','meta') AND (bucket<>'meta' OR item='watch')");
        }
        $update = mpm_query($db, 'UPDATE ' . MPM_REGISTRY . " SET version=?,manifest_sha=? WHERE slug=? AND owner=? AND manifest_sha=? AND state='disabled'",
            [$manifest['version'], $sha, $row['slug'], $row['owner'], $row['manifest_sha']]);
        if ($update->rowCount() !== 1) throw new MpmNotice('به‌روزرسانی ثبت نشد؛ دوباره صفحه را باز کن.');
        $db->commit();
    } catch (Throwable $error) {
        if ($db->inTransaction()) $db->rollBack();
        throw $error;
    }

}

// True for an in-place upgrade; false for a new disabled installation.
function mpm_install(PDO $db, string $dir, string $zip): bool
{
    [$manifest, $raw, $files] = mpm_read_package($zip);
    $slug = $manifest['id'];
    if ($installed = mpm_row($db, $slug)) {
        mpm_upgrade($db, $dir, $installed, $manifest, $raw, $files);
        return true;
    }
    $root = mpm_host_root();
    if ($slug === 'school-classroom' && (is_dir($root . '/online-classroom')
        || str_contains((string)@file_get_contents($root . '/includes/header.php'), 'href="online-classroom/'))) {
        throw new MpmNotice('نسخهٔ دستی کلاس آنلاین پیدا شد. اول آن را طبق راهنما جمع کن؛ مدیر فایل‌های نصب دستی را حذف نمی‌کند.');
    }
    $table = mpm_data_table($slug);
    if (mpm_table_exists($db, $table)) throw new MpmNotice('جدولی با نام اختصاصی افزونه از قبل وجود دارد؛ تغییری نکرد.');
    $row = ['slug' => $slug, 'owner' => bin2hex(random_bytes(16))];
    $owner = $row['owner'];
    if (mpm_table_exists($db, mpm_store_table($row))) throw new MpmNotice('جدول دادهٔ هم‌نام از قبل وجود دارد؛ نصب انجام نشد.');
    [$path, $legacy] = mpm_paths($dir, $row);
    if (mpm_has_directory($path) || mpm_has_directory($legacy)) throw new MpmNotice('پوشهٔ این نصب از قبل وجود دارد؛ فایل‌ها دست‌نخورده ماندند.');
    mpm_plugin_directory($dir, $slug, true);
    mpm_query($db, 'INSERT INTO ' . MPM_REGISTRY . ' (slug,version,manifest_sha,owner,state,installed_at) VALUES (?,?,?,?,?,?)',
        [$slug, $manifest['version'], hash('sha256', $raw), $owner, 'installing', gmdate('c')]);
    try {
        mpm_write_package($path, $raw, $files);
        // Ownership in the table definition survives interruption between CREATE and INSERT.
        // No SQL from ZIP and no IF NOT EXISTS: a matching name alone is never ownership.
        $options = $db->getAttribute(PDO::ATTR_DRIVER_NAME) === 'mysql' ? " ENGINE=InnoDB COMMENT='mtag-plugin:$owner'" : '';
        $db->exec("CREATE TABLE `$table` (id INT PRIMARY KEY, owner CHAR(32) NOT NULL, settings TEXT NULL, runtime VARCHAR(180) NULL)" . $options);
        mpm_query($db, "INSERT INTO `$table` (id,owner) VALUES (1,?)", [$owner]);
        if (!empty($manifest['store'])) mpm_prepare_store($db, $row);
        mpm_state($db, $row, 'disabled');
    } catch (Throwable $error) {
        try {
            mpm_uninstall($db, $dir, $row);
        } catch (Throwable $cleanupError) {
            throw new MpmNotice('نصب کامل نشد؛ پس از بررسی دسترسی، «حذف کامل» را برای پاک‌کردن موارد باقی‌مانده بزن.', 0, $cleanupError);
        }
        throw $error;
    }
    return false;
}

function mpm_manifest_for(string $dir, array $row): array
{
    $path = mpm_release_dir($dir, $row);
    $raw = @file_get_contents($path . '/plugin.json', false, null, 0, 16385);
    if ($raw === false || !hash_equals($row['manifest_sha'], hash('sha256', $raw))) throw new MpmNotice('فهرست افزونه روی هاست موجود یا معتبر نیست.');
    $m = mpm_manifest($raw);
    if ($m['id'] !== $row['slug'] || $m['version'] !== $row['version']) throw new MpmNotice('نسخهٔ نصب‌شده با رجیستری یکسان نیست.');
    return $m;
}

function mpm_verified_file(string $dir, array $row, array $manifest, string $name): string
{
    if (!isset($manifest['files'][$name])) throw new MpmNotice('این مسیر متعلق به بسته نیست.');
    $base = mpm_release_dir($dir, $row);
    $file = $base . '/' . $name;
    $real = realpath($file);
    if (!$real || !mpm_under($real, $base) || is_link($file) || !hash_equals($manifest['files'][$name], hash_file('sha256', $real) ?: '')) {
        throw new MpmNotice('فایل افزونه روی هاست معتبر نیست.');
    }
    return $real;
}

function mpm_owned_row(PDO $db, array $row): array
{
    $table = mpm_data_table($row['slug']);
    $record = mpm_query($db, "SELECT owner,settings,runtime FROM `$table` WHERE id=1")->fetch(PDO::FETCH_ASSOC);
    if (!$record || !hash_equals($row['owner'], $record['owner'])) throw new MpmNotice('مالکیت جدول افزونه تأیید نشد؛ جدول تغییری نکرد.');
    return $record;
}

function mpm_key(string $dir): string
{
    if (!extension_loaded('openssl')) throw new MpmNotice('برای حفاظت تنظیمات افزونه، PHP OpenSSL لازم است.');
    $key = @file_get_contents($dir . '/settings.key', false, null, 0, 33);
    if ($key === false || strlen($key) !== 32) throw new MpmNotice('کلید خصوصی تنظیمات مدیر افزونه‌ها در دسترس نیست.');
    return $key;
}

function mpm_settings(PDO $db, string $dir, array $row): array
{
    $blob = mpm_owned_row($db, $row)['settings'];
    if ($blob === null) return [];
    $decoded = base64_decode($blob, true);
    $data = $decoded !== false && strlen($decoded) > 28
        ? openssl_decrypt(substr($decoded, 28), 'aes-256-gcm', mpm_key($dir), OPENSSL_RAW_DATA,
            substr($decoded, 0, 12), substr($decoded, 12, 16), $row['owner']) : false;
    if ($data === false) throw new MpmNotice('تنظیمات افزونه قابل رمزگشایی نیست.');
    return json_decode($data, true, 32, JSON_THROW_ON_ERROR);
}

function mpm_has_setting(array $manifest, string $key, string $type): bool
{
    foreach ($manifest['settings'] as $field) {
        if ($field['key'] === $key && $field['type'] === $type) return true;
    }
    return false;
}

function mpm_today(?int $now = null): DateTimeImmutable
{
    // This helper is the same source distributed inside the classroom ZIP.
    if (!class_exists(Calendar::class, false)) require_once mpm_host_root() . '/includes/classroom-calendar.php';
    return Calendar::tehran($now ?? time())->setTime(0, 0);
}

function mpm_friday_date(mixed $input, ?int $now = null): string
{
    $today = mpm_today($now);
    $iso = Calendar::fromShamsi($input);
    if ($iso === null) throw new MpmNotice('تاریخ شمسی معتبر را به صورت سال/ماه/روز وارد کن.');
    $date = DateTimeImmutable::createFromFormat('!Y-m-d', $iso, $today->getTimezone());
    if (!$date || $date->format('w') !== '5' || $date < $today) {
        throw new MpmNotice('روز آزمایش باید یک جمعهٔ معتبر باشد که به وقت تهران نگذشته است.');
    }
    return $iso; // Keep saved grants and classroom day/room keys compatible with older versions.
}

function mpm_bell_hours(mixed $input): array
{
    if (!is_array($input) || array_diff_key($input, MPM_BELL_LABELS)) throw new MpmNotice('تنظیم ساعت فقط برای زنگ‌های ۱ تا ۴ مجاز است.');
    $hours = [];
    $previousEnd = null;
    foreach (MPM_BELL_LABELS as $n=>$label) {
        if (!array_key_exists($n, $input)) continue;
        $row = $input[$n];
        if (!is_array($row) || array_diff_key($row, ['start'=>true,'end'=>true])) throw new MpmNotice('تنظیم زنگ ' . $label . ' معتبر نیست.');
        $start = $row['start'] ?? '';
        $end = $row['end'] ?? '';
        if ($start === '' && $end === '') continue;
        foreach ([$start, $end] as $time) {
            if (!is_string($time) || !preg_match('/^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/D', $time)) {
                throw new MpmNotice('برای زنگ ' . $label . ' هر دو ساعت را با قالب ۲۴ ساعته (مثل ۰۸:۳۰) وارد کن، یا هر دو را خالی بگذار.');
            }
        }
        if ($start >= $end) throw new MpmNotice('پایان زنگ ' . $label . ' باید بعد از شروع آن و در همان روز باشد.');
        if ($previousEnd !== null && $start < $previousEnd) {
            throw new MpmNotice('شروع زنگ ' . $label . ' نباید قبل از پایان زنگ قبلی باشد؛ زنگ‌ها را به ترتیب و بدون هم‌پوشانی ثبت کن.');
        }
        $hours[$n] = ['start'=>$start,'end'=>$end];
        $previousEnd = $end;
    }
    return $hours;
}

function mpm_save_settings(PDO $db, string $dir, array $row, array $manifest, array $input): void
{
    $previous = mpm_settings($db, $dir, $row);
    $settings = [];
    foreach ($manifest['settings'] as $field) {
        $key = $field['key'];
        if (in_array($field['type'], ['bell-hours','friday-test'], true)) {
            // Preserve the separately saved bell schedule and Friday opt-in.
            if (isset($previous[$key])) $settings[$key] = $previous[$key];
            continue;
        }
        $value = $input[$key] ?? ($field['type'] === 'select' ? ($previous[$key] ?? $field['default']) : '');
        if (!is_string($value)) throw new MpmNotice('مقدار تنظیم معتبر نیست.');
        $value = trim($value);
        if ($field['type'] === 'class') {
            $id = filter_var($value, FILTER_VALIDATE_INT, ['options' => ['min_range' => 1]]);
            if ($id === false || !mpm_query($db, 'SELECT id FROM classes WHERE id=?', [$id])->fetchColumn()) throw new MpmNotice('یک کلاس موجود را انتخاب کن.');
            $value = $id;
        } elseif ($field['type'] === 'wss') {
            $value = strtolower($value);
            if (!str_starts_with($value, 'wss://') || !mpm_hostname(substr($value, 6))) throw new MpmNotice('آدرس رسانه باید مانند wss://media.example.ir باشد.');
        } elseif ($field['type'] === 'switch') {
            if (!in_array($value, ['', 'yes'], true)) throw new MpmNotice('وضعیت گزینه معتبر نیست.');
            $value = $value === 'yes';
        } elseif ($field['type'] === 'integer') {
            $value = filter_var($value, FILTER_VALIDATE_INT, ['options' => ['min_range' => $field['min'], 'max_range' => $field['max']]]);
            if ($value === false) throw new MpmNotice('عدد تنظیم از محدودهٔ مجاز خارج است.');
        } elseif ($field['type'] === 'select') {
            if (!in_array($value, $field['options'], true)) throw new MpmNotice('این گزینهٔ کیفیت تصویر مجاز نیست.');
        } elseif (in_array($field['type'], ['token', 'secret'], true)) {
            if ($field['type'] === 'secret' && $value === '') $value = $previous[$key] ?? '';
            $optionalRecordingKey = $key === 'recording_secret' && ($input['record_lessons'] ?? '') !== 'yes' && $value === '';
            if (!$optionalRecordingKey && (!preg_match('/^[A-Za-z0-9_-]+$/D', $value) || strlen($value) < $field['min'] || strlen($value) > $field['max'])) throw new MpmNotice('شناسه یا کلید سرویس کامل نیست.');
        } else {
            throw new MpmNotice('مدیر این نوع تنظیم را پشتیبانی نمی‌کند.');
        }
        $settings[$key] = $value;
    }
    if (($settings['record_lessons'] ?? false) && $settings['recording_secret'] === $settings['api_secret']) {
        throw new MpmNotice('کلید سرویس ضبط باید با کلید خصوصی رسانه متفاوت باشد.');
    }
    mpm_write_settings($db, $dir, $row, $settings);
}

function mpm_write_settings(PDO $db, string $dir, array $row, array $settings): void
{
    $iv = random_bytes(12);
    $tag = '';
    $cipher = openssl_encrypt(json_encode($settings, JSON_THROW_ON_ERROR), 'aes-256-gcm', mpm_key($dir), OPENSSL_RAW_DATA, $iv, $tag, $row['owner']);
    if ($cipher === false) throw new MpmNotice('حفاظت تنظیمات ناموفق بود.');
    $table = mpm_data_table($row['slug']);
    mpm_query($db, "UPDATE `$table` SET settings=? WHERE id=1 AND owner=?", [base64_encode($iv . $tag . $cipher), $row['owner']]);
}

function mpm_state(PDO $db, array $row, string $target): void
{
    mpm_query($db, 'UPDATE ' . MPM_REGISTRY . ' SET state=? WHERE slug=? AND owner=?', [$target, $row['slug'], $row['owner']]);
}

function mpm_assert_table_owner(PDO $db, string $table, array $row): void
{
    // Check identity before DROP, even if the first INSERT never happened.
    if ($db->getAttribute(PDO::ATTR_DRIVER_NAME) === 'mysql') {
        $comment = mpm_query($db, "SELECT table_comment FROM information_schema.tables
            WHERE table_schema=DATABASE() AND table_name=? AND table_type='BASE TABLE'", [$table])->fetchColumn();
        if ($comment !== 'mtag-plugin:' . $row['owner']) throw new MpmNotice('جدول هم‌نام متعلق به این نصب نیست؛ جدول تغییری نکرد.');
        $owner = mpm_query($db, "SELECT owner FROM `$table` WHERE id=1")->fetchColumn();
        if ($owner === false || hash_equals($row['owner'], $owner)) return;
        throw new MpmNotice('مالکیت جدول افزونه تأیید نشد؛ جدول تغییری نکرد.');
    }
    mpm_owned_row($db, $row); // SQLite test fixture.
}

// One namespaced transactional store for an installation. Packages never provide SQL to the manager.
function mpm_store_table(array $row): string
{
    if (!preg_match('/^[a-f0-9]{32}$/D', (string)$row['owner'])) throw new MpmNotice('شناسهٔ نصب معتبر نیست.');
    return 'mtg_plug_store_' . $row['owner'];
}

function mpm_check_store(PDO $db, array $row, bool $allowIncomplete = false): string
{
    $table = mpm_store_table($row);
    if ($db->getAttribute(PDO::ATTR_DRIVER_NAME) === 'mysql') {
        $comment = mpm_query($db, "SELECT table_comment FROM information_schema.tables
            WHERE table_schema=DATABASE() AND table_name=? AND table_type='BASE TABLE'", [$table])->fetchColumn();
        if ($comment !== 'mtag-plugin:' . $row['owner']) throw new MpmNotice('جدول دفتر کلاس متعلق به این نصب نیست.');
    } else {
        $allowIncomplete = false; // SQLite fixtures have no ownership comment.
    }
    $owner = mpm_query($db, "SELECT payload FROM `$table` WHERE bucket='!owner' AND item='1'")->fetchColumn();
    if ($owner !== $row['owner'] && !($allowIncomplete && $owner === false)) throw new MpmNotice('مالکیت دفتر کلاس تأیید نشد.');
    return $table;
}

function mpm_prepare_store(PDO $db, array $row): void
{
    $table = mpm_store_table($row);
    if (mpm_table_exists($db, $table)) {
        mpm_check_store($db, $row, true);
        if (mpm_query($db, "SELECT payload FROM `$table` WHERE bucket='!owner' AND item='1'")->fetchColumn() !== false) return;
    } else {
        $options = $db->getAttribute(PDO::ATTR_DRIVER_NAME) === 'mysql'
            ? " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='mtag-plugin:{$row['owner']}'" : '';
        $db->exec("CREATE TABLE `$table` (bucket VARCHAR(80) NOT NULL, item VARCHAR(120) NOT NULL, payload MEDIUMTEXT NOT NULL, PRIMARY KEY(bucket,item))" . $options);
    }
    mpm_query($db, "INSERT INTO `$table` (bucket,item,payload) VALUES ('!owner','1',?)", [$row['owner']]);
}

function mpm_uninstall(PDO $db, string $dir, array $row, ?array $site = null): string
{
    mpm_state($db, $row, 'removing'); // Stop all routes even if clean-up fails and needs a retry.
    $table = mpm_data_table($row['slug']);
    $exists = mpm_table_exists($db, $table);
    $path = mpm_path($dir, $row);
    if ($exists) mpm_assert_table_owner($db, $table, $row);
    $store = mpm_store_table($row);
    if (mpm_table_exists($db, $store)) mpm_check_store($db, $row, true);
    else $store = null;
    $warning = $exists && $site && $row['state'] !== 'installing' ? mpm_deactivate($db, $site, $dir, $row) : '';
    if ($warning !== '' && mpm_native_release($row)) throw new MpmNotice('حذف متوقف شد تا اطلاعات لازم برای بستن اتاق‌ها از بین نرود.' . $warning);
    mpm_remove_tree($path);
    [$current] = mpm_paths($dir, $row);
    if ($path === $current) @rmdir(dirname($current)); // Only remove an empty plugin container.
    if ($store !== null) $db->exec("DROP TABLE `$store`");
    if ($exists) $db->exec("DROP TABLE `$table`");
    mpm_query($db, 'DELETE FROM ' . MPM_REGISTRY . ' WHERE slug=? AND owner=?', [$row['slug'], $row['owner']]);
    return $warning;
}

function mpm_find_role(array $session): ?string
{
    $roles = array_values(array_filter(['admin', 'student', 'teacher'], static fn($role) => !empty($session[$role . '_id'])));
    return count($roles) === 1 ? $roles[0] : null;
}

function mpm_native_release(array $row): bool
{
    return $row['slug'] === 'school-classroom' && version_compare($row['version'], MPM_CLASSROOM_NATIVE, '>=');
}

function mpm_menu_items(PDO $db, string $dir, string $role, string $section = 'menus'): array
{
    $items = [];
    foreach (mpm_query($db, 'SELECT * FROM ' . MPM_REGISTRY . " WHERE state='enabled' ORDER BY slug")->fetchAll(PDO::FETCH_ASSOC) as $row) {
        if (!mpm_native_release($row)) continue;
        try {
            $manifest = mpm_manifest_for($dir, $row);
            $label = $manifest[$section][$role] ?? '';
            if (is_string($label) && $label !== '') $items[$row['slug']] = $label;
        } catch (Throwable $ignored) {
            // Missing or broken plugin: the panel stays available with no link.
        }
    }
    return $items;
}

function mpm_navigation_context(string $role): ?array
{
    // Only read installation metadata, never migrate from a panel request.
    if (!in_array($role, ['admin','deputy','teacher','student'], true)) return null;
    $session = $_SESSION ?? [];
    $sessionRole = $role === 'deputy' ? 'teacher' : $role;
    if (mpm_find_role($session) !== $sessionRole || !($origin = mpm_https_origin())) return null;
    $db = mpm_database();
    $site = mpm_site($db);
    if (!$site || $site['origin'] !== $origin) return null;
    $dir = mpm_private_dir($site['private_dir']);
    $id = filter_var($session[$sessionRole . '_id'], FILTER_VALIDATE_INT, ['options'=>['min_range'=>1]]);
    if ($id === false) return null;
    if ($role === 'admin') {
        $admin = mpm_query($db, 'SELECT role,permissions,status FROM admins WHERE id=?', [$id])->fetch();
        $perms = json_decode((string)($admin['permissions'] ?? ''), true);
        if (!$admin || (int)$admin['status'] !== 1 || ($admin['role'] !== 'super_admin' && (!is_array($perms) || (!in_array('all', $perms, true) && !in_array('manage_classes', $perms, true))))) return null;
    } elseif ($role === 'student') {
        if (mpm_query($db, 'SELECT status FROM students WHERE id=?', [$id])->fetchColumn() !== 'active') return null;
    } else {
        $teacher = mpm_query($db, 'SELECT * FROM teachers WHERE id=?', [$id])->fetch();
        if (!$teacher || (int)$teacher['status'] !== 1 || ($role === 'deputy' && (int)($teacher['is_deputy'] ?? 0) !== 1)) return null;
    }
    return [$db, $dir];
}

function mpm_classroom_tile(string $role): ?array
{
    if (!in_array($role, ['teacher','student'], true)) return null;
    try {
        if (!($context = mpm_navigation_context($role))) return null;
        [$db, $dir] = $context;
        $label = mpm_menu_items($db, $dir, $role)['school-classroom'] ?? null;
        return $label === null ? null : ['href'=>'plugins/school-plugins.php?plugin=school-classroom', 'label'=>$label];
    } catch (Throwable $ignored) {
        return null; // The native dashboard must render even without an installation.
    }
}

function mpm_navigation(string $role): void
{
    try {
        if (!($context = mpm_navigation_context($role))) return;
        [$db, $dir] = $context;
        $items = mpm_menu_items($db, $dir, $role);
        foreach ($items as $slug=>$label) {
            echo '<a class="sidebar-item" href="plugins/school-plugins.php?plugin=' . rawurlencode($slug) . '">' . mpm_html($label) . '</a>';
        }
        foreach (mpm_menu_items($db, $dir, $role, 'report_menus') as $slug => $label) {
            echo '<a class="sidebar-item" href="plugins/school-plugins.php?plugin=' . rawurlencode($slug) . '&amp;view=reports">' . mpm_html($label) . '</a>';
        }
    } catch (Throwable $ignored) {
        // An unavailable plugin must not break the surrounding school application.
    }
}

function mpm_dispatch(PDO $db, array $site, string $slug, string $route, string $jobMode = ''): void
{
    $dir = mpm_private_dir($site['private_dir']);
    mpm_locked($dir, false, static function () use ($db, $site, $dir, $slug, $route, $jobMode): void {
        $row = mpm_row($db, $slug);
        if (!$row || $row['state'] !== 'enabled') {
            http_response_code(404);
            throw new MpmNotice('افزونه نصب نیست یا فعال نشده است.');
        }
        if (!mpm_native_release($row)) {
            http_response_code(409);
            throw new MpmNotice('این نسخه با مسیر جدید سازگار نیست؛ بستهٔ جدید کلاس را به‌روزرسانی کن.');
        }
        $manifest = mpm_manifest_for($dir, $row);
        $role = mpm_find_role($_SESSION ?? []);
        $machineRoute = (in_array($route, ['events','recording'], true) && $slug === 'school-classroom')
            || ($route === 'job' && (($jobMode === 'cli' && PHP_SAPI === 'cli') || $jobMode === 'after-response'));
        if ($route === 'job' && !$machineRoute) {
            http_response_code(404);
            throw new MpmNotice('این صفحه وجود ندارد.');
        }
        if (!$machineRoute && ($role === null || !array_key_exists($role, $manifest['menus']))) {
            http_response_code(403);
            throw new MpmNotice('از پنل دانش‌آموز، دبیر یا مدیر وارد شو.');
        }
        if ($route === 'pdf' && isset($manifest['pdf_resources'])) {
            mpm_pdf_resource($dir, $row, $manifest);
            return;
        }
        if ($route === 'asset') {
            $name = $_GET['name'] ?? '';
            if (!is_string($name) || !preg_match('~^assets/[A-Za-z0-9_.-]+\.(js|mjs|css)$~D', $name, $type)
                || !isset($manifest['files'][$name])) {
                http_response_code(404);
                throw new MpmNotice('فایل در بسته یافت نشد.');
            }
            session_write_close();
            if ($type[1] === 'mjs') header("Content-Security-Policy: default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'");
            header('Content-Type: ' . ($type[1] === 'css' ? 'text/css' : 'text/javascript') . '; charset=UTF-8');
            $digest = $manifest['files'][$name];
            $immutable = mpm_static_versioned($digest);
            $path = mpm_verified_file($dir, $row, $manifest, $name);
            mpm_static_reply($digest, static fn() => file_get_contents($path), $immutable);
            return;
        }
        if (!array_key_exists($route, $manifest['routes'])) {
            http_response_code(404);
            throw new MpmNotice('این صفحه در افزونه وجود ندارد.');
        }
        mpm_call($db, $site, $dir, $row, $manifest, $manifest['routes'][$route], $jobMode);
    });
}

function mpm_accepts_gzip(string $header): bool
{
    $fallback = false;
    foreach (explode(',', strtolower($header)) as $item) {
        [$name, $parameter] = array_pad(array_map('trim', explode(';', $item, 2)), 2, '');
        $accept = $parameter === '' || (preg_match('/^q\s*=\s*([01](?:\.\d{0,3})?)$/D', $parameter, $match)
            && (float)$match[1] > 0 && (float)$match[1] <= 1);
        if ($name === 'gzip') return $accept;
        if ($name === '*') $fallback = $accept;
    }
    return $fallback;
}

function mpm_static_versioned(string $digest): bool
{
    $version = $_GET['v'] ?? null;
    if ($version === null) return false; // Older installed plugins still use revalidated URLs.
    if (!is_string($version) || !hash_equals($digest, $version)) {
        http_response_code(404);
        throw new MpmNotice('این نسخهٔ فایل موجود نیست؛ صفحهٔ کلاس را دوباره باز کن.');
    }
    return true;
}

/** Only package-verified, non-personal assets. Keep lesson APIs, photos and files no-store. */
function mpm_static_reply(string $digest, callable $load, bool $immutable = false, ?callable $loadGzip = null): void
{
    $method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
    if (!in_array($method, ['GET','HEAD'], true)) {
        header('Allow: GET, HEAD'); http_response_code(405); return;
    }
    $tag = '"' . $digest . '"';
    header('Cache-Control: ' . ($immutable ? 'private, max-age=31536000, immutable' : 'private, no-cache'));
    if ($immutable) { header_remove('Pragma'); header_remove('Expires'); }
    header('ETag: W/' . $tag); // Same bytes after decoding gzip or identity.
    header('Vary: Accept-Encoding', false);
    $tags = array_map('trim', explode(',', (string)($_SERVER['HTTP_IF_NONE_MATCH'] ?? '')));
    if (in_array('*', $tags, true) || in_array($tag, $tags, true) || in_array('W/' . $tag, $tags, true)) {
        http_response_code(304); return;
    }
    if ($method === 'HEAD') return;
    // Do not double-compress on a host that already has PHP output compression.
    $gzipOK = (int)ini_get('zlib.output_compression') === 0 && strtolower((string)ini_get('zlib.output_compression')) !== 'on'
        && !in_array('ob_gzhandler', ob_list_handlers(), true) && mpm_accepts_gzip((string)($_SERVER['HTTP_ACCEPT_ENCODING'] ?? ''));
    if ($gzipOK && $loadGzip && is_string($gzip = $loadGzip())) {
        header('Content-Encoding: gzip'); echo $gzip; return;
    }
    $bytes = $load();
    if (!is_string($bytes)) throw new MpmNotice('خواندن فایل بسته ممکن نشد.');
    if ($gzipOK && !$loadGzip && strlen($bytes) > 512 && function_exists('gzencode')) {
        $gzip = gzencode($bytes, 4);
        if ($gzip !== false && strlen($gzip) < strlen($bytes)) {
            header('Content-Encoding: gzip'); echo $gzip; return;
        }
    }
    echo $bytes;
}

/** The trusted ZIP and gzip both use raw DEFLATE. Serve cold requests without inflate/recompress. */
function mpm_pdf_gzip(ZipArchive $zip, array $entry): string|false
{
    if ($entry['comp_method'] !== ZipArchive::CM_DEFLATE || $entry['comp_size'] + 18 >= $entry['size']) return false;
    $raw = $zip->getFromIndex($entry['index'], 0, ZipArchive::FL_COMPRESSED);
    if ($raw === false || strlen($raw) !== $entry['comp_size']) return false;
    return "\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff" . $raw . pack('V2', $entry['crc'], $entry['size']);
}

function mpm_pdf_resource(string $dir, array $row, array $manifest): void
{
    $name = $_GET['name'] ?? null;
    $kinds = ['bcmap'=>'application/octet-stream', 'pfb'=>'application/octet-stream', 'ttf'=>'font/ttf',
        'wasm'=>'application/wasm', 'icc'=>'application/octet-stream', 'js'=>'text/javascript; charset=UTF-8',
        'mjs'=>'text/javascript; charset=UTF-8'];
    if (!is_string($name) || !preg_match('~^(?:(?:cmaps|standard_fonts|wasm|iccs)/[a-zA-Z0-9_.-]+\.(?:bcmap|pfb|ttf|wasm|icc|js)|runtime/pdf(?:\.worker)?\.min\.mjs)$~D', $name)) {
        http_response_code(404); return;
    }
    session_write_close();
    $archive = $manifest['pdf_resources'];
    $digest = $manifest['files'][$archive];
    $immutable = mpm_static_versioned($digest);
    $zip = new ZipArchive();
    if ($zip->open(mpm_verified_file($dir, $row, $manifest, $archive), ZipArchive::RDONLY) !== true) {
        throw new MpmNotice('منابع نمایش PDF معتبر نیست.');
    }
    try {
        $entry = $zip->statName($name);
        if ($entry === false) { http_response_code(404); return; }
        $type = pathinfo($name, PATHINFO_EXTENSION);
        if ($type === 'mjs') header("Content-Security-Policy: default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'");
        header('Content-Type: ' . $kinds[$type]);
        // The verified archive digest also versions every member. No decompression for HEAD/304.
        mpm_static_reply(hash('sha256', $digest . ':' . $name), static fn() => $zip->getFromIndex($entry['index']),
            $immutable, static fn() => mpm_pdf_gzip($zip, $entry));
    } finally {
        $zip->close();
    }
}

function mpm_call(PDO $db, array $site, string $dir, array $row, array $manifest, string $entry, string $jobMode = ''): void
{
    $settings = mpm_settings($db, $dir, $row);
    if ($settings === []) throw new MpmNotice('مدیر باید تنظیمات افزونه را ذخیره کند.');
    foreach ($manifest['files'] as $name => $hash) {
        if (str_ends_with($name, '.php') || $name === 'video-presets.json') mpm_verified_file($dir, $row, $manifest, $name);
    }
    $table = mpm_data_table($row['slug']);
    $GLOBALS['MTAG_PLUGIN_CONTEXT'] = [
        'pdo' => $db, 'base' => 'school-plugins.php?plugin=' . rawurlencode($row['slug']), 'site_base' => '../',
        'site_root' => mpm_host_root(),
        'asset_hashes' => $manifest['files'], 'pdf_hash' => $manifest['files'][$manifest['pdf_resources'] ?? ''] ?? null,
        'store' => !empty($manifest['store']) ? mpm_check_store($db, $row) : null,
        'plugin_dir' => mpm_path($dir, $row), 'job_mode'=>$jobMode,
        'school_bot' => static function (string $provider) use ($db, $site): ?array {
            $adapter = mpm_host_root() . '/includes/classroom-school-bots.php';
            if (!is_file($adapter)) return null;
            require_once $adapter;
            return mpm_school_bot_adapter($db, $provider, mpm_site_base($site['origin']));
        },
        'recording_bot' => static function (string $provider) use ($db): ?array {
            $adapter = mpm_host_root() . '/includes/classroom-school-bots.php';
            if (!is_file($adapter)) return null;
            require_once $adapter;
            return function_exists('mpm_school_recording_bot') ? mpm_school_recording_bot($db, $provider) : null;
        },
        // A pre-upgrade page must reload before calling a new version's API.
        'csrf' => hash_hmac('sha256', $row['owner'] . ':' . $row['manifest_sha'] . ':' . session_id(), mpm_key($dir)),
        'read_runtime' => static fn(): ?string => mpm_owned_row($db, $row)['runtime'],
        'write_runtime' => static function (?string $state) use ($db, $row, $table): void {
            mpm_query($db, "UPDATE `$table` SET runtime=? WHERE id=1 AND owner=?", [$state, $row['owner']]);
        },
        'config' => $settings + ['enabled' => true, 'site_origin' => $site['origin'],
            'room_prefix' => 'test-' . substr(hash('sha256', $site['origin'] . '|' . $row['owner']), 0, 20)],
    ];
    try {
        require mpm_verified_file($dir, $row, $manifest, $entry);
    } finally {
        unset($GLOBALS['MTAG_PLUGIN_CONTEXT']);
    }
}

function mpm_deactivate(PDO $db, array $site, string $dir, array $row): string
{
    // Preserve the runtime marker until the audited hook confirms cleanup.
    try {
        $table = mpm_data_table($row['slug']);
        if (!mpm_table_exists($db, $table) || mpm_owned_row($db, $row)['runtime'] === null) return '';
        $manifest = mpm_manifest_for($dir, $row);
        if (isset($manifest['hooks']['deactivate'])) mpm_call($db, $site, $dir, $row, $manifest, $manifest['hooks']['deactivate']);
        return '';
    } catch (Throwable $error) {
        error_log('mtagbot plugin manager: remote cleanup was not confirmed (' . get_class($error) . ')');
        return ' بسته‌شدن اتاق روی VPS تأیید نشد. اگر جلسه هنوز برقرار است، سرویس رسانهٔ پایلوت را از VPS متوقف کن.';
    }
}

function mpm_is_manager(PDO $db): bool
{
    if (mpm_find_role($_SESSION ?? []) !== 'admin') return false;
    $id = filter_var($_SESSION['admin_id'] ?? null, FILTER_VALIDATE_INT, ['options' => ['min_range' => 1]]);
    if ($id === false) return false;
    $row = mpm_query($db, 'SELECT role,status FROM admins WHERE id=?', [$id])->fetch(PDO::FETCH_ASSOC);
    return $row && (int)$row['status'] === 1 && $row['role'] === 'super_admin';
}

function mpm_post(PDO $db, ?array $site, string $origin, string $csrf): string
{
    $posted = $_POST['csrf'] ?? null;
    if (!is_string($posted) || !hash_equals($csrf, $posted)) {
        http_response_code(403);
        throw new MpmNotice('نشست فرم معتبر نیست؛ صفحه را دوباره باز کن.');
    }
    if (isset($_SERVER['HTTP_ORIGIN']) && $_SERVER['HTTP_ORIGIN'] !== $origin) {
        http_response_code(403);
        throw new MpmNotice('مبدأ فرم با دامنهٔ سایت تست یکسان نیست؛ مدیر افزونه‌ها را مستقیم در همان دامنه باز کن.');
    }
    $action = $_POST['action'] ?? null;
    if ($action === 'initialize') {
        if ($site || ($_POST['confirm_test'] ?? '') !== 'yes') throw new MpmNotice('راه‌اندازی فقط پس از تأیید هاست و دیتابیس تست ممکن است.');
        if (!class_exists('ZipArchive') || !extension_loaded('openssl')) throw new MpmNotice('ابتدا PHP Zip و OpenSSL را در هاست تست فعال کن.');
        $site = mpm_initialize($db, $origin, mpm_storage($origin));
        return 'مدیر آماده است. از این پس ZIP افزونه را در همین تب نصب کن.';
    }
    if (!$site || $site['origin'] !== $origin) throw new MpmNotice('اول مدیر افزونه‌ها را روی دامنهٔ آزمایشی راه‌اندازی کن.');
    $dir = mpm_private_dir($site['private_dir']);
    return mpm_locked($dir, true, static function () use ($db, $site, $dir, $action): string {
        if ($action === 'install') {
            $upload = $_FILES['package'] ?? [];
            $temp = $upload['tmp_name'] ?? null;
            if (($upload['error'] ?? -1) !== UPLOAD_ERR_OK || !is_string($temp) || !is_uploaded_file($temp)) {
                throw new MpmNotice('ZIP دریافت نشد؛ محدودیت آپلود PHP را بررسی کن.');
            }
            if (mpm_install($db, $dir, $temp)) return 'به‌روزرسانی شد؛ تنظیمات رسانه و داده‌ها حفظ شدند. افزونه فعلاً غیرفعال است.';
            return 'نصب شد و هنوز غیرفعال است. تنظیمات را ذخیره و سپس فعال کن.';
        }
        $slug = $_POST['slug'] ?? null;
        $row = is_string($slug) ? mpm_row($db, $slug) : null;
        if (!$row) throw new MpmNotice('افزونه نصب نشده است.');
        if ($action === 'remove') {
            if (($_POST['confirm_delete'] ?? '') !== 'yes') throw new MpmNotice('برای حذف دائمی فایل‌ها و داده‌ها تأیید لازم است.');
            $warning = mpm_uninstall($db, $dir, $row, $site);
            return 'افزونه، فایل‌ها، تنظیمات و جدول اختصاصی آن حذف شدند.' . $warning;
        }
        if (in_array($row['state'], ['installing', 'removing'], true)) throw new MpmNotice('برای این افزونه فقط حذف کامل مجاز است.');
        if ($action === 'disable') {
            mpm_state($db, $row, 'disabled');
            return 'غیرفعال شد؛ داده‌ها و فایل‌ها حفظ شدند.' . mpm_deactivate($db, $site, $dir, $row);
        }
        $manifest = mpm_manifest_for($dir, $row);
        if ($action === 'bell-hours' && mpm_native_release($row) && mpm_has_setting($manifest, 'bell_hours', 'bell-hours')) {
            // The manager lock serializes this update with room requests. Active lessons retain their saved deadline.
            $hours = mpm_bell_hours($_POST['bell_hours'] ?? null);
            $values = mpm_settings($db, $dir, $row);
            if ($hours === []) unset($values['bell_hours']);
            else $values['bell_hours'] = $hours;
            mpm_write_settings($db, $dir, $row, $values);
            return $hours === [] ? 'ساعت‌بندی خاموش شد؛ انتخاب زنگ از برنامهٔ امروز انجام می‌شود.' : 'ساعت زنگ‌ها ذخیره شد؛ زنگی که قبلاً باز شده ساعت پایان قبلی را حفظ می‌کند.';
        }
        if ($action === 'friday-test' && mpm_native_release($row) && mpm_has_setting($manifest, 'friday_test', 'friday-test')) {
            $values = mpm_settings($db, $dir, $row);
            if (($_POST['cancel_friday_test'] ?? null) === 'yes') {
                unset($values['friday_test']);
                $message = 'آزمایش لغو شد؛ ورود تازه به اتاق‌های آزمایشی بسته است.';
            } else {
                $date = mpm_friday_date($_POST['friday_date'] ?? null);
                $withStudents = version_compare($row['version'], MPM_CLASSROOM_PUPIL_TRIAL, '>=') && ($_POST['friday_students'] ?? null) === 'yes';
                $previous = $values['friday_test'] ?? [];
                if (($previous['date'] ?? null) !== $date || (($previous['students'] ?? false) === true) !== $withStudents) {
                    $values['friday_test'] = ['date'=>$date,'id'=>bin2hex(random_bytes(16))];
                    if ($withStudents) $values['friday_test']['students'] = true;
                }
                $message = 'آزمایش فقط برای ' . Calendar::label($date) . '، به وقت تهران، ثبت شد.';
            }
            // Change admission under the same lock before attempting to retire any obsolete demo rooms.
            mpm_write_settings($db, $dir, $row, $values);
            if ($row['state'] === 'enabled' && mpm_owned_row($db, $row)['runtime'] !== null) {
                try { mpm_call($db, $site, $dir, $row, $manifest, $manifest['routes']['job'], 'friday-cleanup'); }
                catch (Throwable $error) {
                    error_log('School classroom: Friday cleanup not confirmed (' . get_class($error) . ')');
                    $message .= ' بستن همهٔ اتاق‌های آزمایشی تأیید نشد؛ پس از وصل‌شدن رسانه دوباره «لغو آزمایش» را بزن.';
                }
            }
            return $message;
        }
        if ($action === 'settings' && is_array($_POST['settings'] ?? null) && mpm_native_release($row)) {
            if ($row['state'] !== 'disabled') throw new MpmNotice('برای تغییر تنظیمات ابتدا افزونه را غیرفعال کن.');
            if (mpm_owned_row($db, $row)['runtime'] !== null) throw new MpmNotice('اتاق قبلی هنوز بسته نشده است؛ ابتدا دوباره بستن اتاق را امتحان کن.');
            mpm_save_settings($db, $dir, $row, $manifest, $_POST['settings']);
            return 'تنظیمات ذخیره شدند.';
        }
        if ($action === 'enable') {
            if ($row['state'] !== 'disabled') throw new MpmNotice('افزونه فعال است.');
            if (!mpm_native_release($row)) throw new MpmNotice('برای فعال‌سازی در این سایت ابتدا ZIP نسخهٔ یکپارچهٔ کلاس را نصب کن.');
            if (mpm_owned_row($db, $row)['runtime'] !== null) throw new MpmNotice('اتاق قبلی هنوز بسته نشده است؛ ابتدا دوباره بستن اتاق را امتحان کن.');
            $values = mpm_settings($db, $dir, $row);
            if (empty($values['signal_url']) || empty($values['api_key']) || empty($values['api_secret'])) throw new MpmNotice('ابتدا تنظیمات رسانه را ذخیره کن.');
            foreach ($manifest['files'] as $name => $hash) mpm_verified_file($dir, $row, $manifest, $name);
            mpm_state($db, $row, 'enabled');
            return 'افزونه فعال شد و پیوندش در پنل‌ها ظاهر می‌شود.';
        }
        throw new MpmNotice('عملیات ناشناخته است.');
    });
}

function mpm_view_start(): void
{
    // With no-referrer, a browser's ordinary same-origin form POST sends Origin: null.
    // same-origin keeps that header usable for validation without sending referrers off-site.
    header('Referrer-Policy: same-origin');
    header('Content-Type: text/html; charset=UTF-8');
    header("Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'");
    echo '<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>مدیر افزونه‌ها</title><style>body{font-family:system-ui,sans-serif;line-height:1.65;max-width:54rem;margin:2rem auto;padding:0 1rem}section{border:1px solid #888;border-radius:8px;margin-block:1rem;padding:1rem}label{display:block;margin:.5rem 0}button,input,select{font:inherit;max-width:100%}input[dir=ltr]{width:min(24rem,95%)}form{margin:.5rem 0}small{display:block}.danger{border-top:1px solid #888;padding-top:.6rem}</style><main><h1>افزونه‌ها</h1><small>نسخهٔ مدیر <bdi>' . MPM_VERSION . '</bdi></small>';
}

function mpm_hidden(string $csrf, string $action, string $slug = ''): void
{
    echo '<input type="hidden" name="csrf" value="' . mpm_html($csrf) . '"><input type="hidden" name="action" value="' . mpm_html($action) . '">';
    if ($slug !== '') echo '<input type="hidden" name="slug" value="' . mpm_html($slug) . '">';
}

function mpm_settings_visible(): bool
{
    try { return mpm_is_manager(mpm_database()); }
    catch (Throwable $ignored) { return false; }
}

// Called before the host header. This is the only HTTP controller for manager actions.
function mpm_prepare_settings(string $formAction = 'other-settings.php?tab=plugins'): array
{
    header('Cache-Control: no-store, private');
    header('Referrer-Policy: same-origin');
    try {
        $db = mpm_database();
        if (!mpm_is_manager($db)) {
            http_response_code(403);
            throw new MpmNotice('تنظیم افزونه‌ها فقط در دسترس مدیر کل فعال مدرسه است.');
        }
        $origin = mpm_origin();
        mpm_site_path();
        $site = mpm_site($db);
        if ($site && $site['origin'] !== $origin) {
            http_response_code(403);
            throw new MpmNotice('این پایگاه به دامنهٔ دیگری متصل است؛ از پایگاه مستقل تست استفاده کن.');
        }
        $csrf = $_SESSION['mpm_csrf'] ??= bin2hex(random_bytes(32));
        if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST') {
            try { $notice = mpm_post($db, $site, $origin, $csrf); }
            catch (MpmNotice $error) { $notice = $error->getMessage(); }
            catch (Throwable $error) {
                error_log('mtagbot plugin manager: action failed (' . get_class($error) . ')');
                $notice = 'عملیات کامل نشد؛ مجوز پایگاه و مسیر خصوصی را بررسی کن.';
            }
            if ((int)http_response_code() >= 400) return ['error'=>$notice];
            $_SESSION['mpm_flash'] = $notice;
            session_write_close();
            header('Location: ' . $formAction, true, 303);
            exit;
        }
        $notice = (string)($_SESSION['mpm_flash'] ?? '');
        unset($_SESSION['mpm_flash']);
        return compact('db','site','csrf','notice');
    } catch (Throwable $error) {
        if ((int)http_response_code() < 400) http_response_code(503);
        return ['error'=>$error instanceof MpmNotice ? $error->getMessage() : 'تنظیمات افزونه‌ها موقتاً در دسترس نیست.'];
    }
}

function mpm_settings_fragment(array $view, string $formAction = 'other-settings.php?tab=plugins'): void
{
    if (isset($view['error'])) { echo '<p role="alert">' . mpm_html($view['error']) . '</p>'; return; }
    try { mpm_render_settings($view['db'], $view['site'], $view['csrf'], $view['notice'], $formAction); }
    catch (Throwable $error) { echo '<p role="alert">تنظیمات افزونه‌ها کامل بارگیری نشد.</p>'; }
}

function mpm_render_bell_hours(string $csrf, string $slug, array $hours, string $action): void
{
    echo '<form class="mb-3" method="post" action="' . $action . '">';
    mpm_hidden($csrf, 'bell-hours', $slug);
    echo '<fieldset aria-describedby="mpm-bell-help"><legend>ساعت زنگ‌های کلاس مجازی</legend>'
        . '<p id="mpm-bell-help">روز، کلاس، درس و دبیر از <a href="courses-management.php?hub_tab=classes#hubPanel" target="_top">مدیریت دروس ← کلاس‌ها و دروس ← برنامه هفتگی و دبیران</a> در سال جاری مدرسه خوانده می‌شوند. اینجا فقط ساعت شروع و پایان هر شمارهٔ زنگ را به وقت تهران ثبت کن؛ ساعت‌ها بین روزها مشترک‌اند. برای زنگی که ساعت ندارد، هر دو خانه را خالی بگذار. اگر همه خالی باشند، محدودیت ساعت خاموش است.</p>'
        . '<div class="table-responsive"><table class="table"><thead><tr><th scope="col">زنگ</th><th scope="col">شروع</th><th scope="col">پایان</th></tr></thead><tbody>';
    foreach (MPM_BELL_LABELS as $n=>$label) {
        echo '<tr><th scope="row">زنگ ' . $label . '</th>';
        foreach (['start'=>'شروع','end'=>'پایان'] as $key=>$title) {
            echo '<td><input type="time" step="60" dir="ltr" class="form-input" name="bell_hours[' . $n . '][' . $key . ']"'
                . ' aria-label="' . $title . ' زنگ ' . $label . '" value="' . mpm_html($hours[$n][$key] ?? '') . '"></td>';
        }
        echo '</tr>';
    }
    echo '</tbody></table></div><p class="text-muted">تغییر ساعت‌ها نیاز به غیرفعال‌کردن افزونه ندارد و زنگ باز را قطع نمی‌کند.</p>'
        . '<button class="btn btn-primary" type="submit">ذخیرهٔ ساعت زنگ‌ها</button></fieldset></form>';
}

function mpm_render_friday_test(string $csrf, string $slug, array $grant, bool $enabled, bool $supportsStudents, string $action): void
{
    $date = $grant['date'] ?? null;
    $today = mpm_today();
    $day = $today->format('Y-m-d');
    $upcoming = $today->format('w') === '5' ? $day : $today->modify('next Friday')->format('Y-m-d');
    $selected = $date !== null && $date >= $day ? $date : $upcoming;
    $status = $date === null ? 'تنظیم نشده' : ($date < $day ? 'پایان یافته' : (!$enabled ? 'در انتظار فعال‌سازی افزونه' : ($date === $day ? 'امروز فعال تا نیمه‌شب' : 'زمان‌بندی‌شده')));
    echo '<section class="card mb-3" id="mpm-friday-test"><h3>آزمایش ۲۴ ساعتهٔ جمعه</h3><p role="status">وضعیت: ' . $status
        . ($date !== null ? ' · ' . mpm_html(Calendar::label($date)) : '') . '</p>'
        . '<p>فقط در تاریخ انتخابی، از ۰۰:۰۰ جمعه تا ۰۰:۰۰ شنبه به وقت تهران. همهٔ دبیران فعال می‌توانند در کلاس‌های سال پیش‌فرض آزمایش را باز کنند یا به آن بپیوندند. برنامهٔ هفتگی، گزارش رسمی و اعلان‌ها تغییری نمی‌کنند.</p>'
        . '<form class="mb-3" method="post" action="' . $action . '">';
    mpm_hidden($csrf, 'friday-test', $slug);
    echo '<label>تاریخ شمسیِ جمعه <input type="text" class="form-input" dir="ltr" lang="fa" required name="friday_date" maxlength="10"'
        . ' aria-describedby="mpm-friday-date-hint" value="' . mpm_html(Calendar::shamsi($selected) ?? '') . '"></label>'
        . '<p id="mpm-friday-date-hint" class="text-muted">سال/ماه/روز، مثلاً ۱۴۰۵/۰۸/۲۹. تاریخ پیشنهادی، نزدیک‌ترین جمعه به وقت تهران است.</p>';
    if ($supportsStudents) {
        echo '<p><label><input type="checkbox" name="friday_students" value="yes"' . (($grant['students'] ?? false) === true && $date === $selected ? ' checked' : '')
            . '> اجازهٔ ورود آزمایشی به دانش‌آموزانِ همان کلاس</label></p>'
            . '<p class="text-muted">دانش‌آموز تا اتصال دبیر در انتظار می‌ماند و تایمر تمرینی او گزارش رسمی نمی‌سازد. تغییر این گزینه، اتاق‌های آزمایشی قبلی را می‌بندد.</p>';
    } else echo '<p class="text-muted">در این نسخه دانش‌آموز وارد آزمایش جمعه نمی‌شود.</p>';
    echo '<button type="submit" class="btn btn-primary">ثبت آزمایش برای این جمعه</button></form><form method="post" action="' . $action . '">';
    mpm_hidden($csrf, 'friday-test', $slug);
    echo '<button type="submit" class="btn btn-outline" name="cancel_friday_test" value="yes">لغو آزمایش</button></form></section>';
}

function mpm_render_settings(PDO $db, ?array $site, string $csrf, string $notice, string $formAction = 'other-settings.php?tab=plugins'): void
{
    $action = mpm_html($formAction);
    echo '<h2>افزونه‌ها</h2><p class="text-muted">مدیر افزونه‌ها · نسخهٔ <bdi>' . MPM_VERSION . '</bdi> · داده‌ها در پایگاه همین سایت</p>';
    if ($notice !== '') echo '<p role="status">' . mpm_html($notice) . '</p>';
    if (!$site) {
        echo '<section class="card mb-3"><h2>راه‌اندازی یک‌باره</h2><form method="post" action="' . $action . '">';
        mpm_hidden($csrf, 'initialize');
        echo '<p>دامنه: <bdi>' . mpm_html(mpm_origin()) . '</bdi>';
        if ($db->getAttribute(PDO::ATTR_DRIVER_NAME) === 'mysql') {
            echo ' · پایگاه متصل: <bdi>' . mpm_html((string)$db->query('SELECT DATABASE()')->fetchColumn()) . '</bdi>';
        }
        echo '</p><label><input required type="checkbox" name="confirm_test" value="yes"> این هاست و پایگاه داده آزمایشی‌اند.</label><button class="btn btn-primary" type="submit">راه‌اندازی مدیر افزونه‌ها</button></form></section>';
        return;
    }
    echo '<section class="card mb-3"><h2>نصب یا به‌روزرسانی از ZIP</h2><form method="post" action="' . $action . '" enctype="multipart/form-data">';
    mpm_hidden($csrf, 'install');
    echo '<label>بستهٔ تأییدشده (حداکثر ۴ مگابایت) <input type="file" name="package" accept=".zip,application/zip" required></label><button class="btn btn-primary" type="submit">نصب یا به‌روزرسانی</button></form></section>';
    $dir = mpm_private_dir($site['private_dir']);
    $rows = mpm_query($db, 'SELECT * FROM ' . MPM_REGISTRY . ' ORDER BY slug')->fetchAll(PDO::FETCH_ASSOC);
    $labels = ['enabled' => 'فعال', 'disabled' => 'غیرفعال', 'installing' => 'نصب ناتمام', 'removing' => 'حذف در انتظار تکمیل'];
    if ($rows === []) echo '<p>افزونه‌ای نصب نشده است.</p>';
    $siteBase = mpm_site_base($site['origin']);
    $rubikaBridge = is_file(mpm_host_root() . '/includes/classroom-rubika-bridge.php');
    foreach ($rows as $row) {
        $slug = $row['slug'];
        $manifest = null;
        $values = [];
        $runtime = null;
        $pending = in_array($row['state'], ['installing', 'removing'], true);
        if (!$pending) {
            try {
                $manifest = mpm_manifest_for($dir, $row);
                $values = mpm_settings($db, $dir, $row);
                $runtime = mpm_owned_row($db, $row)['runtime'];
            } catch (Throwable $ignored) {
                $manifest = null;
            }
        }
        echo '<section class="card mb-3"><h2>' . mpm_html($manifest['title'] ?? $slug) . '</h2><p>نسخهٔ ' . mpm_html($row['version']) . ' · ' . mpm_html($labels[$row['state']] ?? 'نامعتبر') . '</p>';
        $compatible = mpm_native_release($row);
        if ($manifest && $compatible && $row['state'] === 'disabled' && $runtime === null) {
            echo '<form class="mb-3" method="post" action="' . $action . '">';
            mpm_hidden($csrf, 'settings', $slug);
            $group = null;
            foreach ($manifest['settings'] as $field) {
                if (in_array($field['type'], ['bell-hours','friday-test'], true)) continue;
                $nextGroup = $field['group'] ?? null;
                if ($group !== $nextGroup) {
                    if ($group !== null) echo '</fieldset>';
                    $group = $nextGroup;
                    if ($group !== null) echo '<fieldset class="mb-3"><legend>' . mpm_html($group) . '</legend>'
                        . '<p class="text-muted">این کیفیت وقتی کلاس تنظیم جداگانه ندارد یا تنظیم کیفیت در اتاق خاموش است، اعمال می‌شود.</p>';
                }
                $key = $field['key'];
                $name = 'settings[' . mpm_html($key) . ']';
                $value = $values[$key] ?? '';
                echo '<label class="block mb-2">' . mpm_html($field['label']) . ' ';
                if ($field['type'] === 'switch') {
                    echo '<input type="checkbox" name="' . $name . '" value="yes"' . ($value === true ? ' checked' : '') . ($key === 'notify_school_rubika' && !$rubikaBridge ? ' disabled' : '') . '>';
                    if ($key === 'notify_school_rubika' && !$rubikaBridge) echo '<small>اتصال بومی روبیکا در این نسخهٔ سایت تأیید نشده و ارسال نمی‌شود.</small>';
                } elseif ($field['type'] === 'integer') {
                    echo '<input type="number" class="form-input" dir="ltr" name="' . $name . '" value="' . (int)($values[$key] ?? $field['default']) . '" min="' . $field['min'] . '" max="' . $field['max'] . '" required>';
                } elseif ($field['type'] === 'select') {
                    $chosen = $values[$key] ?? $field['default'];
                    echo '<select class="form-input" dir="ltr" name="' . $name . '" required>';
                    foreach ($field['options'] as $option) {
                        $label = $field['option_labels'][$option] ?? (preg_match('/^\d+x\d+$/D', $option) ? str_replace('x', '×', $option) : $option);
                        echo '<option value="' . mpm_html($option) . '"' . ($option === $chosen ? ' selected' : '') . '>' . mpm_html($label) . '</option>';
                    }
                    echo '</select>';
                } elseif ($field['type'] === 'secret') {
                    echo '<input type="password" class="form-input" dir="ltr" name="' . $name . '" autocomplete="new-password"' . ($value === '' && $key !== 'recording_secret' ? ' required' : '') . '>';
                    if ($value !== '') echo '<small>ذخیره شده؛ برای حفظ مقدار فعلی خالی بگذار.</small>';
                } else {
                    echo '<input type="text" class="form-input" dir="ltr" name="' . $name . '" value="' . mpm_html((string)$value) . '" required autocomplete="off">';
                }
                echo '</label>';
            }
            if ($group !== null) echo '</fieldset>';
            echo '<button class="btn btn-primary" type="submit">ذخیرهٔ تنظیمات</button></form>';
        }
        if ($manifest && $compatible && mpm_has_setting($manifest, 'bell_hours', 'bell-hours')) mpm_render_bell_hours($csrf, $slug, $values['bell_hours'] ?? [], $action);
        if ($manifest && $compatible && mpm_has_setting($manifest, 'friday_test', 'friday-test')) {
            mpm_render_friday_test($csrf, $slug, $values['friday_test'] ?? [], $row['state'] === 'enabled',
                version_compare($row['version'], MPM_CLASSROOM_PUPIL_TRIAL, '>='), $action);
        }
        if (!$compatible && !$pending) echo '<p>این نسخهٔ قدیمی را در حالت غیرفعال به بستهٔ یکپارچهٔ کلاس ارتقا بده.</p>';
        if ($compatible && $manifest) {
            echo '<p>گیرندهٔ وب‌هوک LiveKit (در سرور رسانهٔ موجود): <br><code dir="ltr" style="overflow-wrap:anywhere">'
                . mpm_html($siteBase . '/plugins/school-plugins.php?plugin=school-classroom&route=events') . '</code></p>';
            echo '<p class="text-muted">اعلان‌ها فقط به حساب‌های خصوصیِ متصل در ربات فعال همین سایت می‌رسند. برای آزمایش روی نسخهٔ کپی‌شده، آن‌ها را خاموش نگه دار.</p>';
        }
        if ($manifest || $row['state'] === 'enabled') {
            $enabled = $row['state'] === 'enabled';
            $retryRoom = !$enabled && $runtime !== null;
            if ($enabled || $retryRoom || $compatible) {
                echo '<form class="mb-3" method="post" action="' . $action . '">';
                mpm_hidden($csrf, ($enabled || $retryRoom) ? 'disable' : 'enable', $slug);
                echo '<button class="btn btn-primary" type="submit">' . ($enabled ? 'غیرفعال‌سازی (حفظ داده)' : ($retryRoom ? 'تلاش دوباره برای بستن اتاق VPS' : 'فعال‌سازی')) . '</button></form>';
            }
            if ($enabled && isset($manifest['routes']['recording'])) {
                echo '<p><a target="_top" href="plugins/school-plugins.php?plugin=school-classroom&amp;view=recordings">گروه‌های پیام‌رسان و ضبط کلاس</a></p>'
                    . '<p>نشانی سرویس ضبط VPS: <code dir="ltr" style="overflow-wrap:anywhere">'
                    . mpm_html($siteBase . '/plugins/school-plugins.php?plugin=school-classroom&route=recording') . '</code></p>';
            }
            if ($enabled && $compatible) echo '<p><a target="_top" href="plugins/school-plugins.php?plugin=' . rawurlencode($slug) . '">بازکردن افزونه</a></p>';
        } elseif (!$pending) {
            echo '<p>فایل‌ها یا تنظیمات قابل خواندن نیستند. حذف کامل همچنان در دسترس است.</p>';
        }
        echo '<form class="mt-3" method="post" action="' . $action . '">';
        mpm_hidden($csrf, 'remove', $slug);
        echo '<label><input required type="checkbox" name="confirm_delete" value="yes"> حذف دائمی فایل‌ها و داده‌های همین افزونه را تأیید می‌کنم.</label><button class="btn btn-primary" type="submit">حذف کامل افزونه</button></form></section>';
    }
}

function mpm_run(): void
{
    @ini_set('display_errors', '0');
    header('Cache-Control: no-store, private');
    header('Referrer-Policy: no-referrer');
    header('X-Content-Type-Options: nosniff');
    $plugin = $_GET['plugin'] ?? null;
    $route = $_GET['route'] ?? 'page';
    try {
        if ($plugin === null) {
            if (!in_array($_SERVER['REQUEST_METHOD'] ?? '', ['GET','HEAD'], true)) {
                http_response_code(405);
                throw new MpmNotice('مدیریت افزونه‌ها از تب «تنظیمات دیگر ← افزونه‌ها» انجام می‌شود.');
            }
            $tab = is_file(mpm_host_root() . '/school-classroom-settings.php') ? 'hub_tab' : 'tab';
            header('Location: ../other-settings.php?' . $tab . '=plugins', true, 303);
            return;
        }
        $origin = mpm_origin();
        mpm_site_path();
        $root = mpm_host_root();
        if (!mpm_under($root, mpm_document_root()) || !is_file($root . '/includes/auth.php')) throw new MpmNotice('مسیر نصب افزونه معتبر نیست.');
        $db = mpm_database();
        $site = mpm_site($db);
        if ($site && $site['origin'] !== $origin) {
            http_response_code(403);
            throw new MpmNotice('افزونه برای این دامنه فعال نیست.');
        }
        if (!is_string($plugin) || !is_string($route) || !$site) {
            http_response_code(404);
            throw new MpmNotice('این افزونه در دسترس نیست.');
        }
        if (!in_array($route, ['events','job','recording'], true)) require_once $root . '/includes/auth.php';
        mpm_dispatch($db, $site, $plugin, $route);
    } catch (Throwable $error) {
        header('Cache-Control: no-store, private');
        $message = $error instanceof MpmNotice ? $error->getMessage() : 'مدیر افزونه‌ها موقتاً در دسترس نیست.';
        if (!$error instanceof MpmNotice) error_log('mtagbot plugin manager: request failed (' . get_class($error) . ')');
        if ((int)http_response_code() < 400) http_response_code(503);
        if (in_array($route, ['api', 'events', 'recording', 'content', 'asset', 'pdf', 'job'], true)) {
            header('Content-Type: application/json; charset=UTF-8');
            echo json_encode(['ok' => false, 'message' => $message], JSON_UNESCAPED_UNICODE);
        } else {
            mpm_view_start();
            echo '<p role="alert">' . mpm_html($message) . '</p><p><a href="../index.php">بازگشت به سایت</a></p></main></html>';
        }
    }
}

// FastCGI sites can start one delivery after returning the report to the teacher. The host's one-shot cron drains the rest.
function mpm_classroom_delivery_after_response(): void
{
    static $scheduled = false;
    if ($scheduled || !function_exists('fastcgi_finish_request') || !function_exists('get_setting')) return;
    $scheduled = true;
    register_shutdown_function(static function (): void {
        if (!fastcgi_finish_request()) return;
        ignore_user_abort(true);
        try {
            $db = mpm_database();
            $site = mpm_site($db);
            if ($site && $site['origin'] === mpm_https_origin() && (mpm_row($db, 'school-classroom')['state'] ?? '') === 'enabled') {
                mpm_dispatch($db, $site, 'school-classroom', 'job', 'after-response');
            }
        } catch (Throwable $error) {
            error_log('School classroom: deferred delivery did not complete (' . get_class($error) . ')');
        }
    });
}

function mpm_cli(array $args): int
{
    if (PHP_SAPI !== 'cli' || ($args[1] ?? '') !== 'classroom-job' || count($args) > 3) return 2;
    try {
        $GLOBALS['MPM_CLI_SITE_PATH'] = $args[2] ?? '';
        mpm_site_path();
        $db = mpm_database();
        $site = mpm_site($db);
        if (!$site || (mpm_row($db, 'school-classroom')['state'] ?? '') !== 'enabled') return 0;
        $_SERVER['DOCUMENT_ROOT'] = mpm_host_root();
        $_SERVER['HTTPS'] = 'on';
        $_SERVER['HTTP_HOST'] = substr($site['origin'], 8);
        if (mpm_origin() !== $site['origin']) return 2;
        require_once mpm_host_root() . '/includes/functions.php';
        if (session_status() === PHP_SESSION_ACTIVE) session_write_close();
        mpm_dispatch($db, $site, 'school-classroom', 'job', 'cli');
        return 0;
    } catch (Throwable $error) {
        fwrite(STDERR, 'Classroom job unavailable (' . get_class($error) . ")\n");
        return 1;
    }
}

if (realpath((string)($_SERVER['SCRIPT_FILENAME'] ?? '')) === __FILE__) {
    if (PHP_SAPI === 'cli') exit(mpm_cli($argv ?? []));
    mpm_run();
}
