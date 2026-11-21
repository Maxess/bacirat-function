<?php
declare(strict_types=1);

use BaciClassroom\Calendar;

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
    $web = $work . '/public_html';
    $root = $web . '/school-test';
    mkdir($root . '/plugins', 0755, true);
    mkdir($root . '/includes');
    copy(__DIR__ . '/../plugin-manager/plugins/school-plugins.php', $root . '/plugins/school-plugins.php');
    copy(__DIR__ . '/../school-addon/upload/online-classroom/calendar.php', $root . '/includes/classroom-calendar.php');
    require $root . '/plugins/school-plugins.php';
    $_SERVER['DOCUMENT_ROOT'] = $root;
    $_SERVER['HTTP_HOST'] = 'test-school.example.ir';
    $_SERVER['HTTPS'] = 'on';
    $origin = mpm_origin();
    expect(mpm_host_root() === $root, 'nested gateway still locates the site root');
    expect(mpm_storage($origin) === $work . '/.mtagbot-plugins-' . substr(hash('sha256', $root . '|' . $origin), 0, 24), 'storage identity stays compatible with previous managers');
    mkdir($web . '/not-private');
    denied(fn()=>mpm_private_dir($web . '/not-private'));
    $_SERVER['DOCUMENT_ROOT'] = $web;
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
      CREATE TABLE academic_years(id INT PRIMARY KEY, year_name VARCHAR(30), is_default INT,status INT);
      INSERT INTO academic_years VALUES(1,'1404/1405',1,1);
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
    $intermediate = __DIR__ . '/../artifacts/school-classroom-plugin-v0.2.2.zip';
    $upgrade = __DIR__ . '/../artifacts/school-classroom-plugin-v0.2.3.zip';
    $scheduled = __DIR__ . '/../artifacts/school-classroom-plugin-v0.3.0.zip';
    $native = __DIR__ . '/../artifacts/school-classroom-plugin-v0.4.0.zip';
    $timed = __DIR__ . '/../artifacts/school-classroom-plugin-v0.4.1.zip';
    $friday = __DIR__ . '/../artifacts/school-classroom-plugin-v0.4.2.zip';
    $studio = __DIR__ . '/../artifacts/school-classroom-plugin-v0.4.3.zip';
    $streamed = __DIR__ . '/../artifacts/school-classroom-plugin-v0.4.4.zip';
    $compact = __DIR__ . '/../artifacts/school-classroom-plugin-v0.4.5.zip';
    $access = __DIR__ . '/../artifacts/school-classroom-plugin-v0.4.6.zip';
    $focus = __DIR__ . '/../artifacts/school-classroom-plugin-v0.4.7.zip';
    $trail = __DIR__ . '/../artifacts/school-classroom-plugin-v0.4.8.zip';
    $timetable = __DIR__ . '/../artifacts/school-classroom-plugin-v0.4.9.zip';
    $economy = __DIR__ . '/../artifacts/school-classroom-plugin-v0.4.10.zip';
    $localCalendar = __DIR__ . '/../artifacts/school-classroom-plugin-v0.4.11.zip';
    $webcam = __DIR__ . '/../artifacts/school-classroom-plugin-v0.4.12.zip';
    $square = __DIR__ . '/../artifacts/school-classroom-plugin-v0.4.13.zip';
    $quality = __DIR__ . '/../artifacts/school-classroom-plugin-v0.4.14.zip';
    $menus = static fn() => [file_get_contents($root . '/other-settings.php'), file_get_contents($root . '/includes/header.php')];
    $managerMenus = [$other, $header];
    expect($menus() === $managerMenus, 'initialization does not rewrite the host');

    $_SESSION = ['admin_id' => 1]; expect(mpm_is_manager($db), 'super admin authorized');
    $_SESSION = ['admin_id' => 2]; expect(!mpm_is_manager($db), 'regular admin cannot install PHP');
    $_SESSION = ['student_id' => 1]; expect(!mpm_is_manager($db), 'student cannot manage');
    $_SESSION = ['admin_id' => 1];
    foreach (['https://not-the-test-school.example', 'null'] as $badOrigin) {
        $_SERVER['HTTP_ORIGIN'] = $badOrigin;
        denied(fn()=>submit($db, $site, 'enable'));
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
    expect(!is_dir($dir . '/plugins/school-classroom'), 'database collision does not create a plugin folder');
    $db->exec("DROP TABLE `$table`");
    mkdir($root . '/online-classroom');
    file_put_contents($root . '/online-classroom/manual.txt', 'unmanaged');
    denied(fn()=>mpm_install($db, $dir, $upgrade));
    expect(!mpm_row($db, $slug) && file_get_contents($root . '/online-classroom/manual.txt') === 'unmanaged', 'moved manager still detects the separate manual installation');
    unlink($root . '/online-classroom/manual.txt');
    rmdir($root . '/online-classroom');
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

    expect(!mpm_install($db, $dir, $package), 'new install is not an upgrade');
    denied(fn()=>mpm_install($db, $dir, $package));
    $row = mpm_row($db, $slug); $path = mpm_path($dir, $row);
    expect($path === $dir . '/plugins/school-classroom/' . $row['owner'] && $row['state'] === 'disabled' && is_file($path . '/index.php'), 'new install has a dedicated private folder');
    expect(scandir($root . '/plugins') === ['.', '..', 'school-plugins.php'] && !file_exists($root . '/school-plugins.php'), 'no plugin payload or root gateway in the document tree');
    foreach (['student','teacher','admin'] as $role) expect(mpm_menu_items($db, $dir, $role) === [], 'inactive absent from every menu');
    denied(fn()=>mpm_dispatch($db, $site, $slug, 'page'));
    http_response_code(200);
    denied(fn()=>submit($db, $site, 'enable'));
    $secret = str_repeat('x', 64);
    mpm_save_settings($db, $dir, $row, mpm_manifest_for($dir, $row), ['class_id'=>'22', 'signal_url'=>'wss://media.example.ir', 'api_key'=>str_repeat('y',24), 'api_secret'=>$secret]);
    $stored = $db->query("SELECT settings FROM `$table`")->fetchColumn();
    expect(!str_contains($stored, $secret) && mpm_settings($db, $dir, $row)['api_secret'] === $secret, 'encrypted settings round-trip');
    $oldRow = $row;
    $previous = mpm_settings($db, $dir, $row);
    [$newManifest, $newRaw] = mpm_read_package($upgrade);
    $staging = $path . '/release-' . hash('sha256', $newRaw);
    denied(fn()=>submit($db, $site, 'enable')); // Old UI URLs do not work under plugins/.
    mpm_state($db, $row, 'enabled'); // An enabled row left by a previous manager.
    expect(mpm_menu_items($db, $dir, 'student') === [], 'incompatible pages are not linked at the new URL');
    denied(fn()=>mpm_dispatch($db, $site, $slug, 'page'));
    http_response_code(200);
    denied(fn()=>mpm_install($db, $dir, $upgrade));
    expect(mpm_row($db, $slug)['version'] === '0.2.0' && !file_exists($staging), 'active version cannot be replaced');
    submit($db, $site, 'disable');
    mpm_query($db, "UPDATE `$table` SET runtime='pending-cleanup' WHERE id=1");
    denied(fn()=>mpm_install($db, $dir, $upgrade));
    expect(!file_exists($staging) && mpm_owned_row($db, $row)['runtime'] === 'pending-cleanup', 'do not upgrade over unconfirmed remote cleanup');
    mpm_query($db, "UPDATE `$table` SET runtime=NULL WHERE id=1");
    $altered = $work . '/altered-upgrade.zip'; copy($upgrade, $altered);
    $zip = new ZipArchive(); expect($zip->open($altered) === true, 'inspect upgrade fixture');
    $zip->addFromString('media.php', 'not reviewed'); $zip->close();
    denied(fn()=>mpm_install($db, $dir, $altered));
    expect(mpm_manifest_for($dir, $row)['version'] === '0.2.0' && !file_exists($staging), 'tampered upgrade does not disturb previous files');
    mkdir($staging); file_put_contents($staging . '/plugin.json', 'partial write');
    expect(mpm_release_dir($dir, $row) === $path, 'incomplete staging is not served');
    expect(mpm_install($db, $dir, $intermediate), 'previous archive is still approved for in-place upgrade');
    $prior = mpm_row($db, $slug);
    $priorRelease = mpm_release_dir($dir, $prior);
    expect($prior['version'] === '0.2.2' && $priorRelease !== $path && is_dir($priorRelease), 'upgrade starts from release-specific layout');
    $legacyPath = $dir . '/school-classroom-' . $row['owner'];
    expect(rename($path, $legacyPath), 'simulate legacy owner directory with earlier releases');
    clearstatcache(true);
    expect(mpm_release_dir($dir, $prior) === $legacyPath . '/' . basename($priorRelease), 'older storage layout can still be read');
    mkdir($path);
    file_put_contents($path . '/collision', 'keep');
    denied(fn()=>mpm_install($db, $dir, $upgrade));
    expect(is_dir($legacyPath) && file_get_contents($path . '/collision') === 'keep', 'do not overwrite either of two existing paths');
    unlink($path . '/collision'); rmdir($path);
    mkdir($dir . '/plugins/other-plugin');
    file_put_contents($dir . '/plugins/other-plugin/keep.txt', 'independent');
    if (function_exists('symlink') && rmdir(dirname($path))) {
        if (@symlink($dir . '/plugins/other-plugin', dirname($path))) {
            denied(fn()=>mpm_install($db, $dir, $upgrade));
            expect(is_dir($legacyPath) && file_get_contents($dir . '/plugins/other-plugin/keep.txt') === 'independent', 'refuse a symlink in a plugin parent');
            unlink(dirname($path));
        }
    }
    expect(mpm_install($db, $dir, $upgrade), 'checked upgrade reuses the same installation');
    expect(!file_exists($legacyPath) && mpm_path($dir, $prior) === $path && is_dir($priorRelease), 'atomic relocation retains older releases in the dedicated folder');
    $row = mpm_row($db, $slug);
    expect($row['state'] === 'disabled' && $row['version'] === $newManifest['version'] && $row['owner'] === $oldRow['owner'], 'upgrade only switches release pointer');
    expect(mpm_release_dir($dir, $row) === $staging && mpm_manifest_for($dir, $row) === $newManifest, 'staged release verified after retry');
    expect($db->query("SELECT settings FROM `$table`")->fetchColumn() === $stored && mpm_settings($db, $dir, $row) === $previous, 'upgrade keeps settings ciphertext and contents byte-for-byte');
    expect($menus() === $managerMenus && $normal() === $before, 'upgrade does not edit host panels or school records');
    denied(fn()=>mpm_install($db, $dir, $upgrade));
    denied(fn()=>mpm_install($db, $dir, $package));
    denied(fn()=>mpm_dispatch($db, $site, $slug, 'page'));
    http_response_code(200);
    denied(fn()=>submit($db, $site, 'enable')); // Older version needs the native upgrade.
    $store = mpm_store_table($row);
    $db->exec("CREATE TABLE `$store` (bucket VARCHAR(80),item VARCHAR(100),payload TEXT)");
    denied(fn()=>mpm_install($db, $dir, $scheduled));
    expect(mpm_row($db, $slug)['version'] === '0.2.3' && mpm_table_exists($db, $store), 'in-place upgrade will not claim an unrelated table');
    $db->exec("DROP TABLE `$store`");
    expect(mpm_install($db, $dir, $scheduled), 'reviewed scheduled upgrade');
    $row = mpm_row($db, $slug);
    expect($row['version'] === '0.3.0' && mpm_check_store($db, $row) === $store && mpm_settings($db, $dir, $row) === $previous, 'new data store, legacy API keys and owner preserved');
    expect($db->query("SELECT settings FROM `$table`")->fetchColumn() === $stored, 'no decryption/re-entry or rewrite at upgrade');
    denied(fn()=>submit($db, $site, 'enable'));
    mpm_write_settings($db,$dir,$row,$previous + ['max_participants'=>4,'notify_bale'=>true,'notify_rubika'=>true,'rubika_token'=>'LegacySyntheticOnly']);
    foreach (['reports','rubika-link','outbox','notify-pending','meta'] as $bucket) {
        mpm_query($db, "INSERT INTO `$store` (bucket,item,payload) VALUES (?,?,?)", [$bucket,$bucket==='meta'?'watch':'sample','{}']);
    }
    expect(mpm_install($db, $dir, $native), 'installed native update');
    $row = mpm_row($db, $slug);
    expect($row['version'] === '0.4.0' && $row['owner'] === $oldRow['owner'] && mpm_check_store($db,$row)===$store, 'retains owner and attendance data');
    expect(mpm_settings($db,$dir,$row) === ['signal_url'=>$previous['signal_url'],'api_key'=>$previous['api_key'],'api_secret'=>$secret,'max_participants'=>4], 'retains only compatible media configuration; legacy bot options are not enabled');
    expect($db->query("SELECT bucket FROM `$store` WHERE bucket<>'!owner'")->fetchAll(PDO::FETCH_COLUMN) === ['reports'], 'cleans retired watch/pairing/queued data only');
    denied(fn()=>submit($db, $site, 'bell-hours', ['bell_hours'=>[1=>['start'=>'08:30','end'=>'09:15']]]));
    $mediaInput = ['signal_url'=>$previous['signal_url'], 'api_key'=>$previous['api_key'], 'max_participants'=>'4', 'notify_school_bale'=>'yes'];
    submit($db, $site, 'settings', ['settings'=>$mediaInput]);
    $nativeSettings = mpm_settings($db, $dir, $row);
    $nativeCiphertext = mpm_owned_row($db, $row)['settings'];
    submit($db, $site, 'enable');
    denied(fn()=>mpm_install($db, $dir, $timed));
    submit($db, $site, 'disable');
    expect(mpm_install($db, $dir, $timed), 'upgrade the native classroom to optional timed bells');
    $row = mpm_row($db, $slug);
    expect($row['version'] === '0.4.1' && $row['owner'] === $oldRow['owner'] && mpm_check_store($db, $row) === $store, 'timed upgrade keeps the same data ownership');
    expect(mpm_settings($db, $dir, $row) === $nativeSettings && mpm_owned_row($db, $row)['settings'] === $nativeCiphertext
        && $db->query("SELECT payload FROM `$store` WHERE bucket='reports'")->fetchColumn() === '{}', 'timed mode is opt-in; native notices, keys and reports survive');
    submit($db, $site, 'enable');
    $hours = [1=>['start'=>'08:30','end'=>'09:15'], 2=>['start'=>'09:15','end'=>'10:00'], 4=>['start'=>'11:00','end'=>'12:00']];
    $_POST = ['csrf'=>'wrong','action'=>'bell-hours','slug'=>$slug,'bell_hours'=>$hours];
    denied(fn()=>mpm_post($db, $site, $site['origin'], 'fixture-form-token'));
    expect(http_response_code() === 403 && mpm_settings($db, $dir, $row) === $nativeSettings, 'hours-only save still requires the manager session form token');
    http_response_code(200);
    mpm_query($db, "UPDATE `$table` SET runtime='scheduled-v2' WHERE id=1");
    submit($db, $site, 'bell-hours', ['bell_hours'=>$hours + [3=>['start'=>'','end'=>'']], 'settings'=>['api_secret'=>'ignored']]);
    expect(mpm_settings($db, $dir, $row) === $nativeSettings + ['bell_hours'=>$hours] && mpm_owned_row($db, $row)['runtime'] === 'scheduled-v2'
        && mpm_row($db, $slug)['state'] === 'enabled', 'save valid ordered bells with blanks while active without changing media or runtime');
    $hoursCiphertext = mpm_owned_row($db, $row)['settings'];
    foreach ([null, [5=>['start'=>'08:30','end'=>'09:00']], [1=>'08:30'], [1=>['start'=>'08:30','end'=>'']],
        [1=>['start'=>'24:10','end'=>'25:10']], [1=>['start'=>'09:00','end'=>'08:30']], [1=>['start'=>'08:30','end'=>'08:30']],
        [1=>['start'=>'23:30','end'=>'00:30']], [1=>['start'=>['08:30'],'end'=>'09:00']],
        array_replace($hours,[2=>['start'=>'09:14','end'=>'10:00']])] as $invalid) {
        denied(fn()=>submit($db, $site, 'bell-hours', ['bell_hours'=>$invalid]));
        expect(mpm_owned_row($db, $row)['settings'] === $hoursCiphertext, 'reject bad, partial, overlapping and excess periods before writing');
    }
    denied(fn()=>submit($db, $site, 'settings', ['settings'=>$mediaInput]));
    mpm_query($db, "UPDATE `$table` SET runtime=NULL WHERE id=1");
    // The general settings form cannot rewrite the schedule; blanking all rows restores legacy selection.
    submit($db, $site, 'disable');
    submit($db, $site, 'settings', ['settings'=>$mediaInput + ['bell_hours'=>[]]]);
    expect(mpm_settings($db, $dir, $row) === $nativeSettings + ['bell_hours'=>$hours], 'saving general settings keeps the schedule');
    submit($db, $site, 'enable');
    submit($db, $site, 'bell-hours', ['bell_hours'=>[1=>['start'=>'','end'=>'']]]);
    expect(mpm_settings($db, $dir, $row) === $nativeSettings, 'clear all bell hours without changing other settings');
    submit($db, $site, 'bell-hours', ['bell_hours'=>$hours]);
    $today = mpm_today();
    $date = ($today->format('w') === '5' ? $today : $today->modify('next Friday'))->format('Y-m-d');
    $fridayInput = Calendar::shamsi($date);
    $localMidnight = strtotime('2026-11-19T20:30:00Z');
    expect(mpm_friday_date('۱۴۰۵/۰۸/۲۹', $localMidnight - 1)==='2026-11-20'
        && mpm_friday_date('١٤٠٥-٨-٢٩', $localMidnight)==='2026-11-20', 'same civil Friday on both sides of Tehran midnight');
    denied(fn()=>mpm_friday_date('۱۴۰۵/۰۸/۲۹', $localMidnight + 86400));
    denied(fn()=>submit($db, $site, 'friday-test', ['friday_date'=>$fridayInput]));
    denied(fn()=>mpm_install($db, $dir, $friday));
    submit($db, $site, 'disable');
    $saved = mpm_owned_row($db, $row)['settings'];
    expect(mpm_install($db, $dir, $friday), 'upgrade the timed classroom to the Baci-compatible pilot');
    $row = mpm_row($db, $slug);
    expect($row['version']==='0.4.2' && mpm_check_store($db,$row)===$store && mpm_owned_row($db,$row)['settings']===$saved,
        'Baci update preserves reports, keys and four bell times; Friday is still opt-in');
    submit($db, $site, 'enable');
    foreach ([[], $date, '۱۴۰۵/۰۷/۳۱', '۱۳۹۹/۰۸/۳۰', Calendar::shamsi($today->modify('next Thursday')->format('Y-m-d'))] as $invalid) {
        denied(fn()=>submit($db,$site,'friday-test',['friday_date'=>$invalid]));
        expect(mpm_owned_row($db,$row)['settings']===$saved,'invalid or expired Friday does not change configuration');
    }
    $_POST = ['csrf'=>'wrong','action'=>'friday-test','slug'=>$slug,'friday_date'=>$fridayInput];
    denied(fn()=>mpm_post($db,$site,$site['origin'],'fixture-form-token'));
    expect(http_response_code()===403, 'only the manager CSRF form can opt in');
    http_response_code(200);
    $fridayNotice = submit($db,$site,'friday-test',['friday_date'=>$fridayInput]);
    expect(str_contains($fridayNotice, Calendar::label($date)) && !str_contains($fridayNotice, $date), 'the confirmation is also Shamsi');
    $consent = mpm_settings($db,$dir,$row)['friday_test'];
    expect($consent['date']===$date && preg_match('/^[a-f0-9]{32}$/D',$consent['id']) === 1, 'single validated civil date with a generation ID');
    submit($db,$site,'friday-test',['friday_date'=>$fridayInput]);
    expect(mpm_settings($db,$dir,$row)['friday_test']===$consent, 'same date saves without changing the active grant');
    submit($db,$site,'disable');
    submit($db,$site,'settings',['settings'=>$mediaInput + ['friday_test'=>[]]]);
    expect(mpm_settings($db,$dir,$row) === $nativeSettings+['bell_hours'=>$hours,'friday_test'=>$consent], 'ordinary settings cannot overwrite the date or bell hours');
    submit($db,$site,'enable');
    ob_start(); mpm_render_settings($db,$site,'fixture','', 'school-classroom-settings.php?embedded=1'); $pilotUi=ob_get_clean();
    expect(str_contains($pilotUi,'action="school-classroom-settings.php?embedded=1"') && str_contains($pilotUi,'id="mpm-friday-test"') && !str_contains($pilotUi,$consent['id']), 'Baci same-origin hub forms and no generation ID in HTML');
    expect(str_contains($pilotUi, 'تاریخ شمسیِ جمعه') && str_contains($pilotUi, 'value="' . $fridayInput . '"')
        && str_contains($pilotUi, Calendar::label($date)) && !str_contains($pilotUi, $date) && !str_contains($pilotUi, 'type="date"'),
        'prefer a Shamsi input and preserve the exact selected Friday without showing a Gregorian date');
    submit($db,$site,'friday-test',['cancel_friday_test'=>'yes']);
    expect(mpm_settings($db,$dir,$row) === $nativeSettings+['bell_hours'=>$hours], 'early cancellation preserves all normal settings');
    submit($db,$site,'friday-test',['friday_date'=>$fridayInput]);
    $staffGrant = mpm_settings($db,$dir,$row)['friday_test'];
    expect($staffGrant['id']!==$consent['id'], 're-enabling cannot restore a canceled room');
    submit($db,$site,'friday-test',['friday_date'=>$fridayInput,'friday_students'=>'yes']);
    expect(mpm_settings($db,$dir,$row)['friday_test']===$staffGrant, 'older classroom cannot record a grant it does not enforce');
    submit($db,$site,'disable');
    $saved = mpm_owned_row($db,$row)['settings'];
    expect(mpm_install($db,$dir,$studio), 'upgrade to the studio classroom without losing existing policy');
    $row = mpm_row($db,$slug);
    expect($row['version']==='0.4.3' && mpm_check_store($db,$row)===$store && mpm_owned_row($db,$row)['settings']===$saved,
        '0.4.2 to 0.4.3 preserves consent and ordinary data; pupils are still off');
    submit($db,$site,'enable');
    ob_start(); mpm_render_settings($db,$site,'fixture','', 'school-classroom-settings.php?embedded=1'); $studioUi=ob_get_clean();
    expect(str_contains($studioUi,'name="friday_students" value="yes">'), 'pupil checkbox offered but unchecked');
    submit($db,$site,'friday-test',['friday_date'=>$fridayInput,'friday_students'=>'yes']);
    $pupilGrant = mpm_settings($db,$dir,$row)['friday_test'];
    expect($pupilGrant['students'] === true && $pupilGrant['date'] === $date && $pupilGrant['id'] !== $staffGrant['id'], 'pupil opt-in mints a new grant');
    submit($db,$site,'disable');
    $saved = mpm_owned_row($db,$row)['settings'];
    expect(mpm_install($db,$dir,$streamed), 'upgrade to the streamed whiteboard');
    $row = mpm_row($db,$slug);
    expect($row['version']==='0.4.4' && $row['state']==='disabled' && mpm_check_store($db,$row)===$store
        && mpm_owned_row($db,$row)['settings']===$saved && mpm_settings($db,$dir,$row)['friday_test']===$pupilGrant,
        '0.4.3 to 0.4.4 keeps approved pupil practice, media settings, and the report store');
    submit($db,$site,'enable');
    denied(fn()=>mpm_install($db, $dir, $compact));
    submit($db, $site, 'disable');
    expect(mpm_install($db, $dir, $compact), 'upgrade from the streamed whiteboard to the new room');
    $row = mpm_row($db, $slug);
    expect($row['version']==='0.4.5' && $row['owner']===$oldRow['owner'] && mpm_check_store($db,$row)===$store
        && mpm_owned_row($db,$row)['settings']===$saved && mpm_settings($db,$dir,$row)['friday_test']===$pupilGrant
        && $db->query("SELECT payload FROM `$store` WHERE bucket='reports'")->fetchColumn()==='{}',
        '0.4.4 to 0.4.5 keeps owner, report rows, bell hours, media settings, and Friday opt-in');
    submit($db,$site,'enable');
    denied(fn()=>mpm_install($db, $dir, $access));
    submit($db,$site,'disable');
    expect(mpm_install($db,$dir,$access), 'upgrade from 0.4.5 to access controls');
    $row = mpm_row($db,$slug);
    expect($row['version']==='0.4.6' && $row['owner']===$oldRow['owner'] && mpm_check_store($db,$row)===$store
        && mpm_owned_row($db,$row)['settings']===$saved && mpm_settings($db,$dir,$row)['friday_test']===$pupilGrant
        && $db->query("SELECT payload FROM `$store` WHERE bucket='reports'")->fetchColumn()==='{}',
        '0.4.5 to 0.4.6 keeps owner, attendance, settings, hours and Friday opt-in');
    submit($db,$site,'enable');
    denied(fn()=>mpm_install($db,$dir,$focus));
    submit($db,$site,'disable');
    expect(mpm_install($db,$dir,$focus), 'upgrade from access controls to the mobile board');
    $row = mpm_row($db,$slug);
    expect($row['version']==='0.4.7' && $row['owner']===$oldRow['owner'] && mpm_check_store($db,$row)===$store
        && mpm_owned_row($db,$row)['settings']===$saved && mpm_settings($db,$dir,$row)['friday_test']===$pupilGrant
        && (mpm_settings($db,$dir,$row)['screen_share'] ?? false) === false
        && $db->query("SELECT payload FROM `$store` WHERE bucket='reports'")->fetchColumn()==='{}',
        '0.4.6 to 0.4.7 preserves data and screen share is opt-in');
    submit($db,$site,'enable');
    denied(fn()=>mpm_install($db,$dir,$trail));
    submit($db,$site,'disable');
    expect(mpm_install($db,$dir,$trail), 'upgrade to teacher-managed wake and laser trails');
    $row = mpm_row($db,$slug);
    expect($row['version']==='0.4.8' && $row['owner']===$oldRow['owner'] && mpm_check_store($db,$row)===$store
        && mpm_owned_row($db,$row)['settings']===$saved && mpm_settings($db,$dir,$row)['friday_test']===$pupilGrant
        && (mpm_settings($db,$dir,$row)['screen_share'] ?? false) === false
        && $db->query("SELECT payload FROM `$store` WHERE bucket='reports'")->fetchColumn()==='{}',
        '0.4.7 to 0.4.8 conserves data, configuration and the opt-in flag');
    submit($db,$site,'enable');
    denied(fn()=>mpm_install($db,$dir,$timetable));
    submit($db,$site,'disable');
    submit($db,$site,'settings',['settings'=>$mediaInput + ['screen_share'=>'yes']]);
    $savedSettings = mpm_settings($db,$dir,$row);
    $saved = mpm_owned_row($db,$row)['settings'];
    expect($savedSettings['screen_share']===true && $savedSettings['bell_hours']===$hours && $savedSettings['friday_test']===$pupilGrant,
        'current settings include an explicitly enabled screen pilot, bell hours and the pupil grant');
    expect(mpm_install($db,$dir,$timetable), 'upgrade to the native weekly timetable integration');
    $row = mpm_row($db,$slug);
    expect($row['version']==='0.4.9' && $row['owner']===$oldRow['owner'] && mpm_check_store($db,$row)===$store
        && mpm_owned_row($db,$row)['settings']===$saved && mpm_settings($db,$dir,$row)===$savedSettings
        && $db->query("SELECT payload FROM `$store` WHERE bucket='reports'")->fetchColumn()==='{}',
        '0.4.8 to 0.4.9 keeps settings exactly as saved (including screen opt-in) and retains reports');
    submit($db,$site,'enable');
    denied(fn()=>mpm_install($db,$dir,$economy));
    submit($db,$site,'disable');
    expect(mpm_install($db,$dir,$economy), 'upgrade to audio-first mode');
    $row = mpm_row($db,$slug);
    expect($row['version']==='0.4.10' && $row['owner']===$oldRow['owner'] && mpm_check_store($db,$row)===$store
        && mpm_owned_row($db,$row)['settings']===$saved && mpm_settings($db,$dir,$row)===$savedSettings
        && $db->query("SELECT payload FROM `$store` WHERE bucket='reports'")->fetchColumn()==='{}',
        '0.4.9 to 0.4.10 preserves the encrypted media, screen option, bell hours, Friday grant and reports');
    $oldId = str_repeat('a', 32);
    $oldEntry = ['id'=>$oldId, 'class_id'=>22, 'class_name'=>'۸ الف', 'year'=>'1403/1404', 'date'=>'2025-03-20',
        'period'=>'زنگ 1', 'subject'=>'آزمون گزارش قدیمی', 'teacher_name'=>'دبیر آزمایشی', 'active_seconds'=>120];
    $oldKey = sprintf('%012d:%s', strtotime('2025-03-20T05:00:00Z'), $oldId);
    $oldPayload = json_encode($oldEntry, JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
    mpm_query($db, "INSERT INTO `$store` (bucket,item,payload) VALUES (?,?,?)", ['reports',$oldKey,$oldPayload]);
    mpm_query($db, "INSERT INTO `$store` (bucket,item,payload) VALUES (?,?,?)", ['lesson',$oldId,json_encode($oldEntry +
        ['phase'=>'finished', 'teacher'=>7, 'roster'=>['student-1'=>'آزمایشی اول'], 'seconds'=>['student-1'=>95],
         'started'=>strtotime('2025-03-20T05:00:00Z'), 'ended'=>strtotime('2025-03-20T05:02:00Z')], JSON_THROW_ON_ERROR)]);
    submit($db,$site,'enable');
    denied(fn()=>mpm_install($db,$dir,$localCalendar));
    submit($db,$site,'disable');
    expect(mpm_install($db,$dir,$localCalendar), 'install audited package with local calendar');
    $row = mpm_row($db,$slug);
    expect($row['version']==='0.4.11' && $row['owner']===$oldRow['owner'] && mpm_check_store($db,$row)===$store
        && mpm_owned_row($db,$row)['settings']===$saved && mpm_settings($db,$dir,$row)===$savedSettings
        && mpm_query($db, "SELECT payload FROM `$store` WHERE bucket='reports' AND item=?", [$oldKey])->fetchColumn()===$oldPayload,
        '0.4.10 to 0.4.11 leaves historical report rows, ISO keys, permissions and media settings intact');
    submit($db,$site,'enable');
    denied(fn()=>mpm_install($db,$dir,$webcam));
    submit($db,$site,'disable');
    expect(mpm_install($db,$dir,$webcam), 'upgrade the already installed calendar release');
    $row = mpm_row($db,$slug);
    expect($row['version']==='0.4.12' && $row['owner']===$oldRow['owner'] && mpm_check_store($db,$row)===$store && mpm_settings($db,$dir,$row)===$savedSettings
        && mpm_query($db, "SELECT payload FROM `$store` WHERE bucket='reports' AND item=?", [$oldKey])->fetchColumn()===$oldPayload,
        'new video and repeat policies start off without rewriting the old settings');
    ob_start(); mpm_render_settings($db,$site,'fixture',''); $newOptions=ob_get_clean();
    expect(str_contains($newOptions,'name="settings[camera_share]" value="yes">')
        && str_contains($newOptions,'name="settings[repeat_bells]" value="yes">'), 'two separately selectable, unchecked switches');
    denied(fn()=>submit($db,$site,'settings',['settings'=>$mediaInput + ['screen_share'=>'yes','camera_share'=>'on']]));
    submit($db,$site,'settings',['settings'=>$mediaInput + ['screen_share'=>'yes','camera_share'=>'yes','repeat_bells'=>'yes']]);
    $withOpts = mpm_settings($db,$dir,$row);
    expect(($withOpts['camera_share'] ?? null) === true && ($withOpts['repeat_bells'] ?? null) === true
        && $withOpts['screen_share'] === true && $withOpts['friday_test'] === $pupilGrant && $withOpts['bell_hours'] === $hours
        && $withOpts['api_secret'] === $secret, 'opt in without replacing screen option, Tehran hours, saved Friday or key');
    $finishedBefore = mpm_query($db,"SELECT payload FROM `$store` WHERE bucket='lesson' AND item=?",[$oldId])->fetchColumn();
    $encryptedBefore = mpm_owned_row($db,$row)['settings'];
    submit($db,$site,'enable');
    denied(fn()=>mpm_install($db,$dir,$square));
    submit($db,$site,'disable');
    expect(mpm_install($db,$dir,$square), 'update the activated .12 classroom to the approved square webcam release');
    $row = mpm_row($db,$slug);
    expect($row['version']==='0.4.13' && $row['owner']===$oldRow['owner'] && mpm_check_store($db,$row)===$store
        && mpm_settings($db,$dir,$row)===$withOpts && mpm_owned_row($db,$row)['settings']===$encryptedBefore
        && mpm_query($db,"SELECT payload FROM `$store` WHERE bucket='lesson' AND item=?",[$oldId])->fetchColumn()===$finishedBefore,
        '0.4.12 upgrade preserves camera, repeat, screen, keys, hours, Friday, ownership and historic detail bytes');
    submit($db,$site,'enable');
    denied(fn()=>mpm_install($db,$dir,$quality));
    submit($db,$site,'disable');
    expect(mpm_install($db,$dir,$quality), 'upgrade the installed .13 release without removing data');
    $row = mpm_row($db,$slug);
    expect($row['version']==='0.4.14' && $row['owner']===$oldRow['owner'] && mpm_check_store($db,$row)===$store
        && mpm_owned_row($db,$row)['settings']===$encryptedBefore
        && mpm_query($db,"SELECT payload FROM `$store` WHERE bucket='lesson' AND item=?",[$oldId])->fetchColumn()===$finishedBefore,
        'quality controls do not need to migrate settings, store or report data');
    ob_start(); mpm_render_settings($db,$site,'fixture',''); $newOptions=ob_get_clean();
    expect(str_contains($newOptions,'name="settings[room_video_quality]" value="yes">')
        && str_contains($newOptions,'name="settings[student_screen_share]" value="yes">')
        && preg_match_all('/<select[^>]+name="settings\[(?:camera|screen)_(?:low|medium|high)_(?:size|fps)\]"/', $newOptions) === 12
        && str_contains($newOptions,'<option value="128x128" selected>128×128</option>'),
        'two opt-ins are off by default and admin has separate resolution and rate selectors for all six presets');
    $qualityInput = $mediaInput + ['screen_share'=>'yes','camera_share'=>'yes','repeat_bells'=>'yes','room_video_quality'=>'yes',
        'student_screen_share'=>'yes','camera_low_size'=>'32x32','camera_low_fps'=>'5','camera_high_fps'=>'20', 'screen_medium_size'=>'854x480'];
    foreach ([['camera_low_size'=>'640x360'],['screen_medium_size'=>'480x480'],['camera_high_fps'=>'21'],
        ['camera_low_fps'=>'4'],['screen_medium_fps'=>'5.5'],['screen_medium_fps'=>['20']]] as $invalid) {
        denied(fn()=>submit($db,$site,'settings',['settings'=>array_replace($qualityInput,$invalid)]));
    }
    expect(mpm_owned_row($db,$row)['settings']===$encryptedBefore, 'invalid presets do not partially save');
    submit($db,$site,'settings',['settings'=>$qualityInput]);
    $newSettings=mpm_settings($db,$dir,$row);
    expect($newSettings['camera_low_size']==='32x32' && $newSettings['camera_low_fps']==='5' && $newSettings['camera_high_fps']==='20'
        && $newSettings['screen_medium_size']==='854x480' && $newSettings['room_video_quality']===true && $newSettings['student_screen_share']===true
        && $newSettings['friday_test']===$pupilGrant && $newSettings['bell_hours']===$hours && $newSettings['api_secret']===$secret,
        'valid range endpoints, independent presets, intact site settings and credentials');
    submit($db,$site,'enable');
    $_SESSION = ['admin_id'=>1];
    foreach ([['view'=>'reports'], ['view'=>'reports', 'lesson'=>$oldId]] as $reportRequest) {
        $_GET = $reportRequest;
        ob_start(); mpm_dispatch($db,$site,$slug,'page'); $reportHtml = ob_get_clean();
        expect(str_contains($reportHtml, 'پنجشنبه ۱۴۰۳/۱۲/۳۰') && !str_contains($reportHtml, '2025-03-20'), 'old report list and detail use the stored date, including leap Esfand');
        if (isset($reportRequest['lesson'])) expect(str_contains($reportHtml, '۰۰:۰۱:۳۵') && str_contains($reportHtml, '۰۸:۳۰ تا ۰۸:۳۲')
            && str_contains($reportHtml,'اولین حضور تأییدشده (وقت تهران)') && str_contains($reportHtml,'<bdi dir="ltr">—</bdi>'),
            'old report keeps its class clock and duration without backfilling an arrival time');
    }
    submit($db,$site,'friday-test',['friday_date'=>$fridayInput,'friday_students'=>'yes']);
    expect(mpm_settings($db,$dir,$row)['friday_test']===$pupilGrant, 'saving the same pupil policy does not disrupt a test');
    submit($db,$site,'friday-test',['friday_date'=>$fridayInput]);
    $staffOnly = mpm_settings($db,$dir,$row)['friday_test'];
    expect(!isset($staffOnly['students']) && $staffOnly['id'] !== $pupilGrant['id'], 'turning off pupils invalidates their room grant');
    submit($db,$site,'friday-test',['cancel_friday_test'=>'yes']);
    foreach (['student','teacher','admin','deputy'] as $role) expect(isset(mpm_menu_items($db, $dir, $role)[$slug]), 'upgraded release shown in every menu');
    $_SESSION = ['student_id' => 1];
    $_GET = ['class'=>22];
    ob_start(); mpm_dispatch($db, $site, $slug, 'page'); $body = ob_get_clean();
    expect(str_contains($body, 'id="classroom"') && str_contains($body, 'id="notice"') && !str_contains($body, 'id="mic-feedback"') && str_contains($body, 'route=api') && !str_contains($body, $secret), 'current classroom renders without a config file or credential leak');
    expect(str_contains($body, 'href="../student-panel.php"') && str_contains($body, 'data-api="school-plugins.php?plugin='), 'single exit to school and same-gateway API resolve from plugins/');
    expect(substr_count($body,'id="leave"')===1 && str_contains($body,'class="status-dot"')
        && !preg_match('/id="(?:waiting-|live-)?attendance(?:-time)?"/', $body) && !str_contains($body,'>بازگشت به پنل<'),
        'compact room/lobby has a single exit and no standalone attendance element');
    expect(str_contains($body,'class="school-name">never change</bdi>') && str_contains($body,'class="class-name" dir="auto">۸ الف</bdi>')
        && str_contains($body,'id="lesson-start"') && str_contains($body,'id="speaker-strip" aria-label="در حال صحبت" hidden'),
        'room shows the configured school, preserves a custom class label, and starts without voice activity');
    expect(str_contains($body, 'id="pen"') && !str_contains($body, 'id="board-clear"') && !str_contains($body, 'id="toggle-access"'), 'pupils get pen UI but no staff controls');
    expect(!str_contains($body, 'id="room-date"') && !str_contains($body, 'id="receive-video"')
        && str_contains($body, 'data-bell-date="' . mpm_today()->format('Y-m-d') . '"')
        && str_contains($body,'<dialog class="camera-preview" id="camera-preview" aria-labelledby="camera-preview-name">'),
        'preserve Tehran day key and provide the webcam dialog without a visible date or media selector');
    expect(!isset($_SESSION['school_classroom_csrf']), 'managed session leaves no classroom-specific state');
    $old = $_GET ?? []; $_GET['name'] = 'assets/classroom.css';
    ob_start(); mpm_dispatch($db, $site, $slug, 'asset'); $asset = ob_get_clean(); $_GET = $old;
    expect($asset === file_get_contents(mpm_release_dir($dir, $row) . '/assets/classroom.css') && str_contains($asset, '.participant-controls'), 'new release serves its own assets');
    expect(mpm_accepts_gzip('br, gzip; q=0.8') && !mpm_accepts_gzip('gzip;q=0, *;q=1')
        && !mpm_accepts_gzip('br') && !mpm_accepts_gzip('gzip;q=2'), 'gzip respects browser refusal and quality');
    $dig = hash('sha256', $asset);
    $_GET['name'] = 'assets/classroom.css';
    $_SERVER['HTTP_IF_NONE_MATCH'] = 'W/"' . $dig . '"';
    ob_start(); mpm_dispatch($db, $site, $slug, 'asset'); $unchanged = ob_get_clean();
    expect(http_response_code() === 304 && $unchanged === '', 'still validates package and never sends unchanged asset bytes');
    http_response_code(200); unset($_SERVER['HTTP_IF_NONE_MATCH']); $_GET = $old;

    ob_start(); mpm_render_settings($db, $site, 'fixture', ''); $ui = ob_get_clean();
    expect(!str_contains($ui, $secret) && str_contains($ui, 'action="other-settings.php?tab=plugins"') && !str_contains($ui, '<!doctype'), 'native form fragment, no second page or plaintext secret');
    expect(str_contains($ui, 'courses-management.php?hub_tab=classes#hubPanel'), 'clock settings link to the native timetable source');
    expect(substr_count($ui, 'type="time"') === 8 && str_contains($ui, 'name="bell_hours[4][end]" aria-label="پایان زنگ ۴" value="12:00"')
        && str_contains($ui, 'name="bell_hours[3][start]" aria-label="شروع زنگ ۳" value=""') && str_contains($ui, 'name="action" value="bell-hours"'), 'four independent pairs with persisted hours in the enabled settings tab');
    denied(fn()=>submit($db, $site, 'remove'));
    expect(is_file($path . '/index.php') && mpm_table_exists($db, $table), 'explicit delete confirmation');

    submit($db, $site, 'disable');
    expect(mpm_menu_items($db, $dir, 'student') === [] && mpm_table_exists($db, $table) && is_dir($path), 'disable keeps reports and hides classroom');
    expect(mpm_query($db, "SELECT payload FROM `$store` WHERE bucket='reports' AND item=?", [$oldKey])->fetchColumn()===$oldPayload,
        'disable preserves attendance store');

    mpm_query($db, "UPDATE `$store` SET payload='unowned' WHERE bucket='!owner'");
    denied(fn()=>submit($db, $site, 'remove', ['confirm_delete'=>'yes']));
    expect(is_dir($path) && mpm_table_exists($db, $store), 'refuse to purge unowned attendance store');
    mpm_query($db, "UPDATE `$store` SET payload=? WHERE bucket='!owner'", [$row['owner']]);
    $manifestFile = mpm_release_dir($dir,$row) . '/plugin.json';
    $validManifest = file_get_contents($manifestFile);
    mpm_query($db, "UPDATE `$table` SET runtime='scheduled-v2' WHERE id=1");
    file_put_contents($manifestFile, '{}');
    denied(fn()=>submit($db, $site, 'remove', ['confirm_delete'=>'yes']));
    expect(is_dir($path) && mpm_owned_row($db,$row)['runtime']==='scheduled-v2', 'failed media cleanup keeps owned runtime records for retry');
    file_put_contents($manifestFile, $validManifest);
    mpm_query($db, "UPDATE `$table` SET runtime=NULL WHERE id=1");
    // If a table at the expected name is no longer ours, block routes but do not touch it.
    mpm_query($db, "UPDATE `$table` SET owner='not-ours' WHERE id=1");
    denied(fn()=>submit($db, $site, 'remove', ['confirm_delete'=>'yes']));
    expect(mpm_row($db, $slug)['state'] === 'removing' && mpm_table_exists($db, $table), 'unowned table stays intact; plugin inaccessible');
    mpm_query($db, "UPDATE `$table` SET owner=? WHERE id=1", [$row['owner']]);
    file_put_contents($work . '/outside.txt', 'do not delete');
    if (function_exists('symlink')) @symlink($work . '/outside.txt', $path . '/outside-link');
    submit($db, $site, 'remove', ['confirm_delete'=>'yes']);
    expect(!is_dir(dirname($path)) && !mpm_table_exists($db, $table) && !mpm_table_exists($db, $store) && !mpm_row($db, $slug), 'purged dedicated folder, data table, and registry row');
    expect(file_get_contents($dir . '/plugins/other-plugin/keep.txt') === 'independent', 'uninstall preserves the neighboring plugin folder');
    expect($menus() === $managerMenus && file_get_contents($root . '/student-panel.php') === 'unchanged-panel' && $normal() === $before, 'uninstall does not modify any host menu files, panels, or school records');
    expect(file_get_contents($work . '/outside.txt') === 'do not delete', 'unlink without following symlink');
    foreach (['student','teacher','admin'] as $role) expect(mpm_menu_items($db, $dir, $role) === [], 'removed from every panel');
    denied(fn()=>mpm_dispatch($db, $site, $slug, 'asset'));
    mpm_install($db, $dir, $timed);
    expect(mpm_settings($db, $dir, mpm_row($db, $slug)) === [], 'reinstall does not restore deleted data');
    submit($db, $site, 'remove', ['confirm_delete'=>'yes']);
    if ($dsn !== 'sqlite::memory:') {
        mpm_install($db, $dir, $package);
        $attempt = mpm_row($db, $slug);
        mpm_state($db, $attempt, 'installing');
        $db->exec("DELETE FROM `$table` WHERE id=1"); // Crash after DDL, before initial row insertion.
        submit($db, $site, 'remove', ['confirm_delete'=>'yes']);
        expect(!mpm_table_exists($db, $table) && !mpm_row($db, $slug) && !file_exists(mpm_path($dir, $attempt)), 'aborted install recoverable despite partially applied DDL');
        foreach (['classes', 'students', 'admins', 'settings', 'academic_years', MPM_REGISTRY, MPM_SITE] as $fixtureTable) $db->exec("DROP TABLE `$fixtureTable`");
    }
    echo strtoupper($db->getAttribute(PDO::ATTR_DRIVER_NAME)) . " PASS native gateway, private files, guarded migration, bell-hours/Friday settings, active menu state and scoped purge\n";
} finally {
    mpm_remove_tree($work);
}
