<?php
declare(strict_types=1);
require __DIR__ . '/../plugin-manager/school-plugins.php';

function expect(bool $ok, string $what): void {
    if (!$ok) throw new RuntimeException($what);
}
function denied(callable $work): void {
    try { $work(); } catch (MpmNotice $error) { return; }
    throw new RuntimeException('operation should have been rejected');
}
function submit(PDO $db, array $site, string $action, array $data = []): string {
    $_POST = ['csrf' => 'fixture-form-token', 'action' => $action, 'slug' => 'school-classroom'] + $data;
    return mpm_post($db, $site, $site['origin'], 'fixture-form-token');
}

$work = sys_get_temp_dir() . '/mtag-plugin-fixture-' . bin2hex(random_bytes(6));
mkdir($work, 0700);
try {
    $root = $work . '/public_html';
    mkdir($root); mkdir($root . '/includes');
    $_SERVER['DOCUMENT_ROOT'] = $root;
    $_SERVER['HTTP_HOST'] = 'test-school.example.ir';
    $_SERVER['HTTPS'] = 'on';
    $other = '<div class="card integrated-tabs"><a>Before</a></div>';
    $header = "<a href=\"courses-management.php\">admin</a>\n<a href=\"student-panel.php\">student</a>\n<a href=\"teacher-panel.php\">teacher</a>\n";
    file_put_contents($root . '/other-settings.php', $other);
    file_put_contents($root . '/includes/header.php', $header);
    file_put_contents($root . '/student-panel.php', 'unchanged-panel');
    $dsn = getenv('MPM_TEST_DSN') ?: 'sqlite::memory:';
    if ($dsn !== 'sqlite::memory:' && !preg_match('/^mysql:host=127\.0\.0\.1;port=[0-9]+;dbname=mpm_fixture_[a-z0-9_]+;charset=utf8mb4$/D', $dsn)) {
        throw new RuntimeException('Only a disposable local mpm_fixture_* database may be used');
    }
    $db = new PDO($dsn, getenv('MPM_TEST_USER') ?: null, getenv('MPM_TEST_PASSWORD') ?: null, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
    if ($dsn !== 'sqlite::memory:' && $db->query('SHOW TABLES')->fetchColumn() !== false) throw new RuntimeException('Fixture database must be empty');
    $db->exec("CREATE TABLE classes(id INT PRIMARY KEY,name TEXT,academic_year TEXT);
      CREATE TABLE students(id INT PRIMARY KEY,first_name TEXT,last_name TEXT,class_name TEXT,academic_year TEXT,status TEXT);
      CREATE TABLE admins(id INT PRIMARY KEY,name TEXT,role TEXT,permissions TEXT,status INT);
      CREATE TABLE settings(key_name TEXT,key_value TEXT);
      INSERT INTO classes VALUES(22,'۸ الف','1404/1405');
      INSERT INTO students VALUES(1,'آزمایشی','اول','۸ الف','1404/1405','active');
      INSERT INTO admins VALUES(1,'مدیر کل','super_admin','[]',1),(2,'مدیر','admin','[]',1);
      INSERT INTO settings VALUES('school_name','never change');");
    $normal = static fn() => array_map(static fn($n) => $db->query('SELECT * FROM ' . $n)->fetchAll(PDO::FETCH_ASSOC), ['classes','students','admins','settings']);
    $before = $normal();
    $site = mpm_initialize($db, mpm_origin(), $work . '/private');
    $dir = $site['private_dir'];
    $slug = 'school-classroom';
    $table = mpm_data_table($slug);
    $package = __DIR__ . '/../artifacts/school-classroom-plugin-v0.2.0.zip';
    mpm_link_menus($root, $dir);
    $menus = static fn() => [file_get_contents($root . '/other-settings.php'), file_get_contents($root . '/includes/header.php')];
    $managerMenus = $menus();
    expect(str_contains($managerMenus[0], 'mpm-host-tab-v1') && substr_count($managerMenus[1], 'mpm-host-nav-v1-') === 3, 'generic hooks in all panels');
    expect(file_get_contents($dir . '/menu-before-other-settings.php') === $other && file_get_contents($dir . '/menu-before-header.php') === $header, 'menu backups');
    mpm_link_menus($root, $dir);
    expect($menus() === $managerMenus, 'idempotent menu setup');
    denied(fn()=>mpm_templates($other, '<a href="student-panel.php">only one anchor</a>'));

    $_SESSION = ['admin_id' => 1]; expect(mpm_is_manager($db), 'super admin authorized');
    $_SESSION = ['admin_id' => 2]; expect(!mpm_is_manager($db), 'regular admin cannot install PHP');
    $_SESSION = ['student_id' => 1]; expect(!mpm_is_manager($db), 'student cannot manage');
    $_SESSION = ['admin_id' => 1];
    foreach (['https://not-the-test-school.example', 'null'] as $badOrigin) {
        $_SERVER['HTTP_ORIGIN'] = $badOrigin;
        denied(fn()=>submit($db, $site, 'menus'));
        expect(http_response_code() === 403, 'invalid Origin remains blocked despite a valid form token');
    }
    $_SERVER['HTTP_ORIGIN'] = $site['origin'];
    $_POST = ['csrf' => 'wrong', 'action' => 'remove'];
    denied(fn()=>mpm_post($db, $site, $site['origin'], 'fixture-form-token'));
    expect(http_response_code() === 403, 'same-origin form still needs a valid token');
    http_response_code(200);

    $db->exec("CREATE TABLE `$table` (id INT PRIMARY KEY, owner TEXT,settings TEXT); INSERT INTO `$table` VALUES(1,'unrelated','keep');");
    denied(fn()=>mpm_install($db, $dir, $package));
    expect($db->query("SELECT settings FROM `$table`")->fetchColumn() === 'keep' && !mpm_row($db, $slug), 'never take or drop an existing table');
    expect(glob($dir . '/school-classroom-*') === [], 'failed install cleaned its own files');
    $db->exec("DROP TABLE `$table`");
    foreach (['wrong-manifest','tampered-file','extra-file'] as $bad) {
        $copy = $work . '/' . $bad . '.zip'; copy($package, $copy);
        $zip = new ZipArchive(); expect($zip->open($copy) === true, 'inspect fixture');
        if ($bad === 'wrong-manifest') $zip->addFromString('plugin.json', '{}');
        if ($bad === 'tampered-file') $zip->addFromString('index.php', 'different payload');
        if ($bad === 'extra-file') $zip->addFromString('extra.txt', 'not in manifest');
        $zip->close();
        denied(fn()=>mpm_install($db, $dir, $copy));
        expect(!mpm_row($db, $slug) && !mpm_table_exists($db, $table), 'rejected before creating files or tables');
    }

    mpm_install($db, $dir, $package);
    denied(fn()=>mpm_install($db, $dir, $package));
    $row = mpm_row($db, $slug); $path = mpm_path($dir, $row);
    expect($row['state'] === 'disabled' && is_file($path . '/index.php'), 'install disabled and private');
    foreach (['student','teacher','admin'] as $role) expect(mpm_menu_items($db, $dir, $role) === [], 'inactive absent from every menu');
    denied(fn()=>mpm_dispatch($db, $site, $slug, 'page'));
    http_response_code(200);
    denied(fn()=>submit($db, $site, 'enable'));
    $secret = str_repeat('x', 64);
    submit($db, $site, 'settings', ['settings' => ['class_id'=>'22', 'signal_url'=>'wss://media.example.ir', 'api_key'=>str_repeat('y',24), 'api_secret'=>$secret]]);
    $stored = $db->query("SELECT settings FROM `$table`")->fetchColumn();
    expect(!str_contains($stored, $secret) && mpm_settings($db, $dir, $row)['api_secret'] === $secret, 'encrypted settings round-trip');
    submit($db, $site, 'enable');
    foreach (['student','teacher','admin'] as $role) expect(isset(mpm_menu_items($db, $dir, $role)[$slug]), 'active shown in every menu');
    $_SESSION = ['student_id' => 1];
    ob_start(); mpm_dispatch($db, $site, $slug, 'page'); $body = ob_get_clean();
    expect(str_contains($body, 'id="classroom"') && str_contains($body, 'route=api') && !str_contains($body, $secret), 'managed classroom renders without a config file or credential leak');
    expect(!isset($_SESSION['school_classroom_csrf']), 'managed session leaves no classroom-specific state');
    $old = $_GET ?? []; $_GET['name'] = 'assets/classroom.css';
    ob_start(); mpm_dispatch($db, $site, $slug, 'asset'); $asset = ob_get_clean(); $_GET = $old;
    expect($asset === file_get_contents($path . '/assets/classroom.css'), 'managed asset routed');
    ob_start(); mpm_admin_page($db, $site, 'fixture', ''); $ui = ob_get_clean();
    expect(!str_contains($ui, $secret), 'manager never echoes the saved secret');
    denied(fn()=>submit($db, $site, 'remove'));
    expect(is_file($path . '/index.php') && mpm_table_exists($db, $table), 'explicit delete confirmation');

    submit($db, $site, 'disable');
    expect(mpm_menu_items($db, $dir, 'student') === [] && mpm_table_exists($db, $table) && is_dir($path), 'disable only hides');
    submit($db, $site, 'enable');
    // If a table at the expected name is no longer ours, block routes but do not touch it.
    mpm_query($db, "UPDATE `$table` SET owner='not-ours' WHERE id=1");
    denied(fn()=>submit($db, $site, 'remove', ['confirm_delete'=>'yes']));
    expect(mpm_row($db, $slug)['state'] === 'removing' && mpm_table_exists($db, $table), 'unowned table stays intact; plugin inaccessible');
    mpm_query($db, "UPDATE `$table` SET owner=? WHERE id=1", [$row['owner']]);
    file_put_contents($work . '/outside.txt', 'do not delete');
    if (function_exists('symlink')) @symlink($work . '/outside.txt', $path . '/outside-link');
    submit($db, $site, 'remove', ['confirm_delete'=>'yes']);
    expect(!is_dir($path) && !mpm_table_exists($db, $table) && !mpm_row($db, $slug), 'purged files, data table, and registry row');
    expect($menus() === $managerMenus && file_get_contents($root . '/student-panel.php') === 'unchanged-panel' && $normal() === $before, 'uninstall does not modify any host menu files, panels, or school records');
    expect(file_get_contents($work . '/outside.txt') === 'do not delete', 'unlink without following symlink');
    foreach (['student','teacher','admin'] as $role) expect(mpm_menu_items($db, $dir, $role) === [], 'removed from every panel');
    denied(fn()=>mpm_dispatch($db, $site, $slug, 'asset'));
    mpm_install($db, $dir, $package);
    expect(mpm_settings($db, $dir, mpm_row($db, $slug)) === [], 'reinstall does not restore deleted data');
    submit($db, $site, 'remove', ['confirm_delete'=>'yes']);
    if ($dsn !== 'sqlite::memory:') {
        mpm_install($db, $dir, $package);
        $attempt = mpm_row($db, $slug);
        mpm_state($db, $attempt, 'installing');
        $db->exec("DELETE FROM `$table` WHERE id=1"); // Crash after DDL, before initial row insertion.
        submit($db, $site, 'remove', ['confirm_delete'=>'yes']);
        expect(!mpm_table_exists($db, $table) && !mpm_row($db, $slug) && !file_exists(mpm_path($dir, $attempt)), 'aborted install recoverable despite partially applied DDL');
        foreach (['classes', 'students', 'admins', 'settings', MPM_REGISTRY, MPM_SITE] as $fixtureTable) $db->exec("DROP TABLE `$fixtureTable`");
    }
    echo strtoupper($db->getAttribute(PDO::ATTR_DRIVER_NAME)) . " PASS plugin admin gate, reviewed ZIP only, real dispatch, hide/disable, complete owned file+data+menu uninstall, unchanged site, clean reinstall\n";
} finally {
    mpm_remove_tree($work);
}
