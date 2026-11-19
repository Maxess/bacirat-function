<?php
// Save as school-classroom.local.php next to (not inside) the website document root.
// Enable only on the test domain. Never commit the filled-in file.
return [
    'enabled' => false,
    'site_origin' => 'https://test-school.example.ir',
    'signal_url' => 'wss://media.example.ir',
    'expected_test_database' => '', // Exact name of the separate test MySQL database.
    'class_id' => 0, // Replace with the numeric classes.id on the test database.
    'room_prefix' => 'test-school', // Make this unique for the test installation.
    'api_key' => '',
    'api_secret' => '',
];
