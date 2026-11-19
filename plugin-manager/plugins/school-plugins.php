<?php
declare(strict_types=1);

// Standalone mtagbot plugin host. Deploy as plugins/school-plugins.php.
const MPM_VERSION = '0.1.4';
const MPM_REGISTRY = 'mtg_plugin_registry';
const MPM_SITE = 'mtg_plugin_site';
const MPM_MAX_ZIP = 2 * 1024 * 1024;
const MPM_CATALOG = [
    'school-classroom' => [
        '0.2.0' => '67f080645d4dce5b709183556b0dd7297c0784388942b2dd00c6157d8a5a117b',
        '0.2.1' => '0cb2f12254559d3b92c925abb3e9244328a3caf9c3386325c8f7d11b0c8c7ea6',
        '0.2.2' => 'ac0eff553b77293722b3c594dac867c7ed1135b16e3c27d5a776a6d013f75931',
        '0.2.3' => 'a9f39ab9a6b1aeb062973f2489b750f3245443b099ffff77915f555dfa92295b',
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
    $file = mpm_host_root() . '/config/database.php';
    $cfg = is_file($file) ? require $file : null;
    if (!is_array($cfg) || ($cfg['driver'] ?? 'mysql') !== 'mysql' || empty($cfg['database'])) {
        throw new MpmNotice('اتصال به پایگاه MySQL سایت آزمایشی تنظیم نشده است.');
    }
    $host = $cfg['host'] ?? '127.0.0.1';
    $port = $cfg['port'] ?? 3306;
    $name = $cfg['database'];
    $charset = $cfg['charset'] ?? 'utf8mb4';
    return new PDO("mysql:host={$host};port={$port};dbname={$name};charset={$charset}",
        $cfg['username'] ?? '', $cfg['password'] ?? '', [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_EMULATE_PREPARES => false]);
}

function mpm_origin(): string
{
    $host = strtolower((string)($_SERVER['HTTP_HOST'] ?? ''));
    if (!in_array(strtolower((string)($_SERVER['HTTPS'] ?? '')), ['on', '1'], true) || !mpm_hostname($host)) {
        http_response_code(403);
        throw new MpmNotice('پایلوت فقط روی دامنهٔ HTTPS سایت آزمایشی فعال می‌شود.');
    }
    return 'https://' . $host;
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
    if (($manifest['format'] ?? null) !== 1 || !is_array($manifest['files'] ?? null) || count($manifest['files']) > 24) {
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
    if (!is_file($path) || filesize($path) > MPM_MAX_ZIP) throw new MpmNotice('حجم ZIP نباید بیشتر از ۲ مگابایت باشد.');
    $zip = new ZipArchive();
    if ($zip->open($path, ZipArchive::RDONLY) !== true) throw new MpmNotice('فایل ZIP قابل خواندن نیست.');
    try {
        $stat = $zip->statName('plugin.json');
        if ($zip->numFiles > 25 || !$stat || $stat['size'] > 16384) throw new MpmNotice('این ZIP بستهٔ قابل نصب مدیر افزونه‌ها نیست.');
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
            if ($size < 0 || $size > 1024 * 1024 || $total > 2 * 1024 * 1024) throw new MpmNotice('اندازهٔ بازشدهٔ بسته بیش از حد مجاز است.');
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
    if ($manifest['format'] !== $previous['format'] || $manifest['settings'] !== $previous['settings']) {
        throw new MpmNotice('ساختار تنظیمات نسخهٔ جدید با نصب فعلی سازگار نیست؛ تنظیمات تغییر نکرد.');
    }
    $sha = hash('sha256', $raw);
    $staged = mpm_relocate($dir, $row) . '/release-' . $sha;
    // A retry may find an interrupted write of this exact reviewed release; never replace active files.
    mpm_remove_tree($staged);
    try {
        mpm_write_package($staged, $raw, $files);
    } catch (Throwable $error) {
        mpm_remove_tree($staged);
        throw $error;
    }
    // Switch the pointer only after the files are written. Keep staged files on an uncertain SQL result.
    // Neither the owner nor the settings table/AEAD binding changes. Uninstall removes the entire owner root.
    $update = mpm_query($db, 'UPDATE ' . MPM_REGISTRY . " SET version=?,manifest_sha=? WHERE slug=? AND owner=? AND manifest_sha=? AND state='disabled'",
        [$manifest['version'], $sha, $row['slug'], $row['owner'], $row['manifest_sha']]);
    if ($update->rowCount() !== 1) throw new MpmNotice('به‌روزرسانی ثبت نشد؛ دوباره صفحه را باز کن.');
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

function mpm_save_settings(PDO $db, string $dir, array $row, array $manifest, array $input): void
{
    $previous = mpm_settings($db, $dir, $row);
    $settings = [];
    foreach ($manifest['settings'] as $field) {
        $key = $field['key'];
        $value = $input[$key] ?? '';
        if (!is_string($value)) throw new MpmNotice('مقدار تنظیم معتبر نیست.');
        $value = trim($value);
        if ($field['type'] === 'class') {
            $id = filter_var($value, FILTER_VALIDATE_INT, ['options' => ['min_range' => 1]]);
            if ($id === false || !mpm_query($db, 'SELECT id FROM classes WHERE id=?', [$id])->fetchColumn()) throw new MpmNotice('یک کلاس موجود را انتخاب کن.');
            $value = $id;
        } elseif ($field['type'] === 'wss') {
            $value = strtolower($value);
            if (!str_starts_with($value, 'wss://') || !mpm_hostname(substr($value, 6))) throw new MpmNotice('آدرس رسانه باید مانند wss://media.example.ir باشد.');
        } elseif (in_array($field['type'], ['token', 'secret'], true)) {
            if ($field['type'] === 'secret' && $value === '') $value = $previous[$key] ?? '';
            if (!preg_match('/^[A-Za-z0-9_-]+$/D', $value) || strlen($value) < $field['min'] || strlen($value) > $field['max']) throw new MpmNotice('شناسه یا کلید سرویس رسانه کامل نیست.');
        } else {
            throw new MpmNotice('مدیر این نوع تنظیم را پشتیبانی نمی‌کند.');
        }
        $settings[$key] = $value;
    }
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

function mpm_uninstall(PDO $db, string $dir, array $row, ?array $site = null): string
{
    mpm_state($db, $row, 'removing'); // Stop all routes even if clean-up fails and needs a retry.
    $table = mpm_data_table($row['slug']);
    $exists = mpm_table_exists($db, $table);
    $path = mpm_path($dir, $row);
    if ($exists) mpm_assert_table_owner($db, $table, $row);
    $warning = $exists && $site && $row['state'] !== 'installing' ? mpm_deactivate($db, $site, $dir, $row) : '';
    mpm_remove_tree($path);
    [$current] = mpm_paths($dir, $row);
    if ($path === $current) @rmdir(dirname($current)); // Only remove an empty plugin container.
    if ($exists) $db->exec("DROP TABLE `$table`");
    mpm_query($db, 'DELETE FROM ' . MPM_REGISTRY . ' WHERE slug=? AND owner=?', [$row['slug'], $row['owner']]);
    return $warning;
}

function mpm_find_role(array $session): ?string
{
    $roles = array_values(array_filter(['admin', 'student', 'teacher'], static fn($role) => !empty($session[$role . '_id'])));
    return count($roles) === 1 ? $roles[0] : null;
}

function mpm_supports_nested_gateway(array $row): bool
{
    // Earlier audited classroom packages assume their page lives beside the host panels.
    return $row['slug'] !== 'school-classroom' || version_compare($row['version'], '0.2.3', '>=');
}

function mpm_menu_items(PDO $db, string $dir, string $role): array
{
    $items = [];
    foreach (mpm_query($db, 'SELECT * FROM ' . MPM_REGISTRY . " WHERE state='enabled' ORDER BY slug")->fetchAll(PDO::FETCH_ASSOC) as $row) {
        if (!mpm_supports_nested_gateway($row)) continue;
        try {
            $manifest = mpm_manifest_for($dir, $row);
            $label = $manifest['menus'][$role] ?? '';
            if (is_string($label) && $label !== '') $items[$row['slug']] = $label;
        } catch (Throwable $ignored) {
            // Missing or broken plugin: the panel stays available with no link.
        }
    }
    return $items;
}

function mpm_navigation(string $role): void
{
    // This hook is generic and read-only: never create tables or load a plugin's PHP here.
    try {
        if (mpm_find_role($_SESSION ?? []) !== $role) return;
        $origin = mpm_origin();
        $db = DB::getInstance()->getPdo();
        if (!$db instanceof PDO || $db->getAttribute(PDO::ATTR_DRIVER_NAME) !== 'mysql') return;
        $site = mpm_site($db);
        if (!$site || $site['origin'] !== $origin) return;
        $dir = mpm_private_dir($site['private_dir']);
        foreach (mpm_menu_items($db, $dir, $role) as $slug => $label) {
            echo '<a class="sidebar-item" href="plugins/school-plugins.php?plugin=' . rawurlencode($slug) . '">' . mpm_html($label) . '</a>';
        }
    } catch (Throwable $ignored) {
        // The host panels must not depend on this subsystem being operational.
    }
}

// Replace only the exact hook emitted by previous releases; never rewrite arbitrary links.
function mpm_update_hook(string $source, string $marker, string $old): ?string
{
    if (!str_contains($source, $marker)) return null;
    $new = str_replace('school-plugins.php', 'plugins/school-plugins.php', $old);
    if (substr_count($source, $marker) === 1) {
        if (str_contains($source, $new)) return $source;
        if (str_contains($source, $old)) return str_replace($old, $new, $source);
    }
    throw new MpmNotice('پیوند قبلی مدیر افزونه‌ها تغییر کرده است؛ هیچ فایل منو بازنویسی نشد.');
}

function mpm_templates(string $settings, string $header): array
{
    $tab = <<<'HTML'
<?php /* mpm-host-tab-v1 */ if (($_SESSION['admin_role'] ?? '') === 'super_admin' && is_file(__DIR__ . '/school-plugins.php')): ?><a class="btn btn-outline" href="school-plugins.php">افزونه‌ها</a><?php endif; ?>
HTML;
    $patched = mpm_update_hook($settings, 'mpm-host-tab-v1', $tab);
    if ($patched === null) {
        $anchor = '<div class="card integrated-tabs">';
        if (substr_count($settings, $anchor) !== 1) throw new MpmNotice('چیدمان «تنظیمات دیگر» ناشناخته است؛ هیچ فایل منو بازنویسی نشد.');
        $settings = str_replace($anchor, $anchor . str_replace('school-plugins.php', 'plugins/school-plugins.php', $tab), $settings);
    } else {
        $settings = $patched;
    }
    foreach (['admin' => 'courses-management', 'student' => 'student-panel', 'teacher' => 'teacher-panel'] as $role => $page) {
        $hook = <<<'HTML'
<?php /* mpm-host-nav-v1-ROLE */ if (is_file(dirname(__DIR__) . '/school-plugins.php')) { try { require_once dirname(__DIR__) . '/school-plugins.php'; mpm_navigation('ROLE'); } catch (Throwable $ignored) {} } ?>
HTML;
        $hook = str_replace('ROLE', $role, $hook);
        $patched = mpm_update_hook($header, 'mpm-host-nav-v1-' . $role, $hook);
        if ($patched !== null) {
            $header = $patched;
            continue;
        }
        $hook = str_replace('school-plugins.php', 'plugins/school-plugins.php', $hook);
        $pattern = '~^([ \t]*)<a href="' . preg_quote($page, '~') . '\.php"[^\n]*</a>\r?$~m';
        $header = preg_replace_callback($pattern, static fn($m) => $m[0] . "\n" . $m[1] . $hook, $header, -1, $count);
        if ($count !== 1) throw new MpmNotice('چیدمان یکی از پنل‌ها ناشناخته است؛ هیچ فایل منو بازنویسی نشد.');
    }
    return [$settings, $header];
}

function mpm_atomic_replace(string $path, string $contents, int $mode): void
{
    $temp = dirname($path) . '/.mpm-menu-' . bin2hex(random_bytes(8)) . '.php';
    try {
        mpm_new_file($temp, $contents);
        if (!@chmod($temp, $mode) || !@rename($temp, $path)) throw new MpmNotice('اجازهٔ نوشتن فایل منوی سایت تست وجود ندارد.');
        if (function_exists('opcache_invalidate')) @opcache_invalidate($path, true);
    } finally {
        if (is_file($temp)) @unlink($temp);
    }
}

function mpm_link_menus(string $root, string $dir): void
{
    $resolved = realpath($root);
    if (!$resolved) throw new MpmNotice('ریشهٔ سایت معتبر نیست.');
    $locations = [$resolved . '/other-settings.php', $resolved . '/includes/header.php'];
    $original = [];
    $modes = [];
    foreach ($locations as $path) {
        // Refuse symlinks out of the test site (or into the production site).
        if (realpath($path) !== $path || !is_file($path)) throw new MpmNotice('فایل منو داخل همین سایت تست پیدا نشد.');
        $original[] = file_get_contents($path);
        $modes[] = fileperms($path) & 0777;
    }
    $next = mpm_templates($original[0], $original[1]); // Preflight both before modifying either.
    $changed = [];
    try {
        foreach ($locations as $i => $path) {
            if ($next[$i] === $original[$i]) continue;
            $backup = $dir . '/menu-before-' . basename($path);
            if (!is_file($backup)) mpm_new_file($backup, $original[$i]);
            if (file_get_contents($path) !== $original[$i]) throw new MpmNotice('فایل منو هم‌زمان تغییر کرد؛ دوباره تلاش کن.');
            mpm_atomic_replace($path, $next[$i], $modes[$i]);
            $changed[] = $i;
        }
    } catch (Throwable $error) {
        foreach (array_reverse($changed) as $i) {
            if (file_get_contents($locations[$i]) === $next[$i]) mpm_atomic_replace($locations[$i], $original[$i], $modes[$i]);
        }
        throw $error;
    }
}

function mpm_dispatch(PDO $db, array $site, string $slug, string $route): void
{
    $dir = mpm_private_dir($site['private_dir']);
    mpm_locked($dir, false, static function () use ($db, $site, $dir, $slug, $route): void {
        $row = mpm_row($db, $slug);
        if (!$row || $row['state'] !== 'enabled') {
            http_response_code(404);
            throw new MpmNotice('افزونه نصب نیست یا فعال نشده است.');
        }
        if (!mpm_supports_nested_gateway($row)) {
            http_response_code(409);
            throw new MpmNotice('این نسخه با مسیر جدید سازگار نیست؛ بستهٔ جدید کلاس را به‌روزرسانی کن.');
        }
        $manifest = mpm_manifest_for($dir, $row);
        $role = mpm_find_role($_SESSION ?? []);
        if ($role === null || !array_key_exists($role, $manifest['menus'])) {
            http_response_code(403);
            throw new MpmNotice('از پنل دانش‌آموز، دبیر یا مدیر وارد شو.');
        }
        if ($route === 'asset') {
            $name = $_GET['name'] ?? '';
            if (!is_string($name) || !preg_match('~^assets/[A-Za-z0-9_.-]+\.(js|css)$~D', $name, $type)
                || !isset($manifest['files'][$name])) {
                http_response_code(404);
                throw new MpmNotice('فایل در بسته یافت نشد.');
            }
            session_write_close();
            header('Content-Type: ' . ($type[1] === 'css' ? 'text/css' : 'text/javascript') . '; charset=UTF-8');
            readfile(mpm_verified_file($dir, $row, $manifest, $name));
            return;
        }
        if (!in_array($route, ['page', 'api'], true)) {
            http_response_code(404);
            throw new MpmNotice('این صفحه در افزونه وجود ندارد.');
        }
        mpm_call($db, $site, $dir, $row, $manifest, $manifest['routes'][$route]);
    });
}

function mpm_call(PDO $db, array $site, string $dir, array $row, array $manifest, string $entry): void
{
    $settings = mpm_settings($db, $dir, $row);
    if ($settings === []) throw new MpmNotice('مدیر باید تنظیمات افزونه را ذخیره کند.');
    foreach ($manifest['files'] as $name => $hash) {
        if (str_ends_with($name, '.php')) mpm_verified_file($dir, $row, $manifest, $name);
    }
    $table = mpm_data_table($row['slug']);
    $GLOBALS['MTAG_PLUGIN_CONTEXT'] = [
        'pdo' => $db, 'base' => 'school-plugins.php?plugin=' . rawurlencode($row['slug']), 'site_base' => '../',
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
    // Remote runtime is best-effort. A VPS outage must never block deletion on the web host.
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
        mpm_locked($site['private_dir'], true, static fn() => mpm_link_menus(mpm_host_root(), $site['private_dir']));
        return 'مدیر آماده است. از این پس ZIP افزونه را همین‌جا نصب کن.';
    }
    if (!$site || $site['origin'] !== $origin) throw new MpmNotice('اول مدیر افزونه‌ها را روی دامنهٔ آزمایشی راه‌اندازی کن.');
    $dir = mpm_private_dir($site['private_dir']);
    return mpm_locked($dir, true, static function () use ($db, $site, $dir, $action): string {
        if ($action === 'menus') {
            mpm_link_menus(mpm_host_root(), $dir);
            return 'پیوند تنظیمات و پنل‌ها آماده است.';
        }
        if ($action === 'install') {
            $upload = $_FILES['package'] ?? [];
            $temp = $upload['tmp_name'] ?? null;
            if (($upload['error'] ?? -1) !== UPLOAD_ERR_OK || !is_string($temp) || !is_uploaded_file($temp)) {
                throw new MpmNotice('ZIP دریافت نشد؛ محدودیت آپلود PHP را بررسی کن.');
            }
            if (mpm_install($db, $dir, $temp)) return 'به‌روزرسانی شد؛ تنظیمات حفظ شدند. افزونه فعلاً غیرفعال است.';
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
        if ($action === 'settings' && is_array($_POST['settings'] ?? null)) {
            if ($row['state'] !== 'disabled') throw new MpmNotice('برای تغییر تنظیمات ابتدا افزونه را غیرفعال کن.');
            if (mpm_owned_row($db, $row)['runtime'] !== null) throw new MpmNotice('اتاق قبلی هنوز بسته نشده است؛ ابتدا دوباره بستن اتاق را امتحان کن.');
            mpm_save_settings($db, $dir, $row, $manifest, $_POST['settings']);
            return 'تنظیمات ذخیره شدند.';
        }
        if ($action === 'enable') {
            if ($row['state'] !== 'disabled') throw new MpmNotice('افزونه فعال است.');
            if (!mpm_supports_nested_gateway($row)) throw new MpmNotice('برای فعال‌سازی در مسیر جدید، ابتدا ZIP جدید کلاس را نصب کن.');
            if (mpm_owned_row($db, $row)['runtime'] !== null) throw new MpmNotice('اتاق قبلی هنوز بسته نشده است؛ ابتدا دوباره بستن اتاق را امتحان کن.');
            if (mpm_settings($db, $dir, $row) === []) throw new MpmNotice('ابتدا تنظیمات را ذخیره کن.');
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

function mpm_admin_page(PDO $db, ?array $site, string $csrf, string $notice): void
{
    mpm_view_start();
    echo '<p><a href="../other-settings.php">بازگشت به تنظیمات دیگر</a></p>';
    if ($notice !== '') echo '<p role="status">' . mpm_html($notice) . '</p>';
    if (!$site) {
        echo '<section><h2>راه‌اندازی یک‌باره</h2><form method="post" action="school-plugins.php">';
        mpm_hidden($csrf, 'initialize');
        echo '<p>دامنه: <bdi>' . mpm_html(mpm_origin()) . '</bdi>';
        if ($db->getAttribute(PDO::ATTR_DRIVER_NAME) === 'mysql') {
            echo ' · پایگاه متصل: <bdi>' . mpm_html((string)$db->query('SELECT DATABASE()')->fetchColumn()) . '</bdi>';
        }
        echo '</p><label><input required type="checkbox" name="confirm_test" value="yes"> این هاست و پایگاه داده آزمایشی‌اند.</label><button>راه‌اندازی مدیر افزونه‌ها</button></form></section></main></html>';
        return;
    }
    echo '<section><h2>نصب یا به‌روزرسانی از ZIP</h2><form method="post" action="school-plugins.php" enctype="multipart/form-data">';
    mpm_hidden($csrf, 'install');
    echo '<label>بستهٔ تأییدشده (حداکثر ۲ مگابایت) <input type="file" name="package" accept=".zip,application/zip" required></label><button>نصب یا به‌روزرسانی</button></form></section>';
    $dir = mpm_private_dir($site['private_dir']);
    $rows = mpm_query($db, 'SELECT * FROM ' . MPM_REGISTRY . ' ORDER BY slug')->fetchAll(PDO::FETCH_ASSOC);
    $labels = ['enabled' => 'فعال', 'disabled' => 'غیرفعال', 'installing' => 'نصب ناتمام', 'removing' => 'حذف در انتظار تکمیل'];
    if ($rows === []) echo '<p>افزونه‌ای نصب نشده است.</p>';
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
        echo '<section><h2>' . mpm_html($manifest['title'] ?? $slug) . '</h2><p>نسخهٔ ' . mpm_html($row['version']) . ' · ' . mpm_html($labels[$row['state']] ?? 'نامعتبر') . '</p>';
        if ($manifest && $row['state'] === 'disabled' && $runtime === null) {
            echo '<form method="post" action="school-plugins.php">';
            mpm_hidden($csrf, 'settings', $slug);
            foreach ($manifest['settings'] as $field) {
                $key = $field['key'];
                $name = 'settings[' . mpm_html($key) . ']';
                $value = $values[$key] ?? '';
                echo '<label>' . mpm_html($field['label']) . ' ';
                if ($field['type'] === 'class') {
                    echo '<select name="' . $name . '" required><option value="">انتخاب کلاس</option>';
                    foreach (mpm_query($db, 'SELECT id,name,academic_year FROM classes ORDER BY academic_year DESC,id LIMIT 500')->fetchAll(PDO::FETCH_ASSOC) as $class) {
                        echo '<option value="' . (int)$class['id'] . '"' . ((int)$value === (int)$class['id'] ? ' selected' : '') . '>' . mpm_html($class['name'] . ' — ' . $class['academic_year']) . '</option>';
                    }
                    echo '</select>';
                } elseif ($field['type'] === 'secret') {
                    echo '<input type="password" dir="ltr" name="' . $name . '" autocomplete="new-password"' . ($value === '' ? ' required' : '') . '>';
                    if ($value !== '') echo '<small>ذخیره شده؛ برای حفظ مقدار فعلی خالی بگذار.</small>';
                } else {
                    echo '<input type="text" dir="ltr" name="' . $name . '" value="' . mpm_html((string)$value) . '" required autocomplete="off">';
                }
                echo '</label>';
            }
            echo '<button>ذخیرهٔ تنظیمات</button></form>';
        }
        $compatible = mpm_supports_nested_gateway($row);
        if (!$compatible && !$pending) echo '<p>برای استفاده در مسیر جدید، کلاس را به نسخهٔ ۰٫۲٫۳ یا بالاتر به‌روزرسانی کن.</p>';
        if ($manifest || $row['state'] === 'enabled') {
            $enabled = $row['state'] === 'enabled';
            $retryRoom = !$enabled && $runtime !== null;
            if ($enabled || $retryRoom || $compatible) {
                echo '<form method="post" action="school-plugins.php">';
                mpm_hidden($csrf, ($enabled || $retryRoom) ? 'disable' : 'enable', $slug);
                echo '<button>' . ($enabled ? 'غیرفعال‌سازی (حفظ داده)' : ($retryRoom ? 'تلاش دوباره برای بستن اتاق VPS' : 'فعال‌سازی')) . '</button></form>';
            }
            if ($enabled && $compatible) echo '<p><a href="school-plugins.php?plugin=' . rawurlencode($slug) . '">بازکردن افزونه</a></p>';
        } elseif (!$pending) {
            echo '<p>فایل‌ها یا تنظیمات قابل خواندن نیستند. حذف کامل همچنان در دسترس است.</p>';
        }
        echo '<form class="danger" method="post" action="school-plugins.php">';
        mpm_hidden($csrf, 'remove', $slug);
        echo '<label><input required type="checkbox" name="confirm_delete" value="yes"> حذف دائمی فایل‌ها و داده‌های همین افزونه را تأیید می‌کنم.</label><button>حذف کامل افزونه</button></form></section>';
    }
    echo '<form method="post" action="school-plugins.php">';
    mpm_hidden($csrf, 'menus');
    echo '<button>ترمیم پیوندهای عمومی مدیر افزونه‌ها</button></form></main></html>';
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
        $origin = mpm_origin();
        $root = mpm_host_root();
        if (basename(__DIR__) !== 'plugins' || !mpm_under($root, mpm_document_root()) || !is_file($root . '/includes/functions.php')) {
            throw new MpmNotice('این فایل را در plugins/school-plugins.php داخل سایت تست قرار بده.');
        }
        $legacy = $root . '/school-plugins.php';
        if (is_link($legacy) || (is_file($legacy) && str_contains((string)@file_get_contents($legacy, false, null, 0, 1024), 'const MPM_REGISTRY = '))) {
            throw new MpmNotice('مدیر قدیمی هنوز در ریشهٔ سایت است؛ پس از بکاپ، فقط همان نسخهٔ قدیمی را از مسیر عمومی بردار.');
        }
        require_once $root . '/includes/functions.php';
        $db = mpm_database();
        $site = mpm_site($db);
        if ($site && $site['origin'] !== $origin) {
            http_response_code(403);
            throw new MpmNotice('مدیر افزونه‌ها برای این دامنه فعال نیست.');
        }
        if ($plugin !== null) {
            if (!is_string($plugin) || !is_string($route) || !$site) {
                http_response_code(404);
                throw new MpmNotice('این افزونه در دسترس نیست.');
            }
            mpm_dispatch($db, $site, $plugin, $route);
            return;
        }
        if (!mpm_is_manager($db)) {
            http_response_code(403);
            throw new MpmNotice('ابتدا در سایت تست با حساب مدیر کل وارد شو.');
        }
        $csrf = $_SESSION['mpm_csrf'] ??= bin2hex(random_bytes(32));
        $notice = (string)($_SESSION['mpm_flash'] ?? '');
        unset($_SESSION['mpm_flash']);
        if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'POST') {
            try {
                $notice = mpm_post($db, $site, $origin, $csrf);
            } catch (MpmNotice $error) {
                $notice = $error->getMessage();
            } catch (Throwable $error) {
                error_log('mtagbot plugin manager: action failed (' . get_class($error) . ')');
                $notice = 'عملیات کامل نشد؛ دسترسی پوشهٔ خصوصی یا مجوز ساخت و حذف جدول‌ها را بررسی کن.';
            }
            $_SESSION['mpm_flash'] = $notice;
            session_write_close();
            header('Location: school-plugins.php', true, 303);
            return;
        }
        session_write_close();
        mpm_admin_page($db, $site, $csrf, $notice);
    } catch (Throwable $error) {
        $message = $error instanceof MpmNotice ? $error->getMessage() : 'مدیر افزونه‌ها موقتاً در دسترس نیست.';
        if (!$error instanceof MpmNotice) error_log('mtagbot plugin manager: request failed (' . get_class($error) . ')');
        if ((int)http_response_code() < 400) http_response_code(503);
        if ($route === 'api') {
            header('Content-Type: application/json; charset=UTF-8');
            echo json_encode(['ok' => false, 'message' => $message], JSON_UNESCAPED_UNICODE);
        } else {
            mpm_view_start();
            echo '<p role="alert">' . mpm_html($message) . '</p><p><a href="../index.php">بازگشت به سایت</a></p></main></html>';
        }
    }
}

if (realpath((string)($_SERVER['SCRIPT_FILENAME'] ?? '')) === __FILE__) mpm_run();
