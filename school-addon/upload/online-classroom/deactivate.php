<?php
declare(strict_types=1);
require_once __DIR__ . '/core.php';

// Only the manager calls this while holding its exclusive lifecycle lock.
$context = cl_manager_context();
if ($context === null) return;
$cfg = cl_config();
cl_managed_action($cfg, $context, 'close', null,
    static fn(string $method, string $room, array $data): array => cl_rpc($cfg, $room, $method, $data));
