<?php
declare(strict_types=1);
require_once __DIR__ . '/notifications.php';
if (PHP_SAPI !== 'cli') throw new ClassroomError(404, '');
cl_delivery_job(cl_store(), cl_config());
