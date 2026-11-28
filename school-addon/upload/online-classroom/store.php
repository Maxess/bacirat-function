<?php
declare(strict_types=1);
require_once __DIR__ . '/core.php';

final class ClassroomStore
{
    public function __construct(public readonly PDO $db, private readonly string $table)
    {
        if (!preg_match('/^mtg_plug_store_[a-f0-9]{32}$/D', $table)) throw new LogicException('Unowned classroom table');
    }

    public function get(string $bucket, string $id): ?array
    {
        $value = cl_query($this->db, "SELECT payload FROM `$this->table` WHERE bucket=? AND item=?", [$bucket, $id])->fetchColumn();
        return $value === false ? null : json_decode($value, true, 32, JSON_THROW_ON_ERROR);
    }

    public function put(string $bucket, string $id, array $value): void
    {
        $payload = json_encode($value, JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
        $upsert = $this->db->getAttribute(PDO::ATTR_DRIVER_NAME) === 'sqlite'
            ? 'ON CONFLICT(bucket,item) DO UPDATE SET payload=excluded.payload' : 'ON DUPLICATE KEY UPDATE payload=VALUES(payload)';
        cl_query($this->db, "INSERT INTO `$this->table` (bucket,item,payload) VALUES (?,?,?) $upsert", [$bucket, $id, $payload]);
    }

    public function delete(string $bucket, ?string $id = null): void
    {
        cl_query($this->db, "DELETE FROM `$this->table` WHERE bucket=?" . ($id === null ? '' : ' AND item=?'),
            $id === null ? [$bucket] : [$bucket, $id]);
    }

    public function rows(string $bucket, int $limit, string $cursor = '', bool $reverse = false): array
    {
        $dir = $reverse ? 'DESC' : 'ASC';
        $op = $reverse ? '<' : '>';
        $rows = cl_query($this->db, "SELECT item,payload FROM `$this->table` WHERE bucket=? AND item $op ? ORDER BY item $dir LIMIT " . max(1, min(1000, $limit)), [$bucket, $cursor]);
        $result = [];
        while ($row = $rows->fetch(PDO::FETCH_ASSOC)) $result[$row['item']] = json_decode($row['payload'], true, 32, JSON_THROW_ON_ERROR);
        return $result;
    }

    public function scan(string $bucket, int $page = 128): Generator
    {
        $cursor = '';
        while ($rows = $this->rows($bucket, $page, $cursor)) {
            yield from $rows;
            $cursor = (string)array_key_last($rows);
        }
    }

    /** An index whose item key is also the target's key. Preserve missing rows to advance the cursor. */
    public function linkedRows(string $index, string $bucket, int $limit, string $cursor): array
    {
        $rows = cl_query($this->db, "SELECT i.item,j.payload FROM `$this->table` i
            LEFT JOIN `$this->table` j ON j.bucket=? AND j.item=i.item
            WHERE i.bucket=? AND i.item>? ORDER BY i.item LIMIT " . max(1, min(1000, $limit)), [$bucket, $index, $cursor]);
        $result = [];
        while ($row = $rows->fetch(PDO::FETCH_ASSOC)) {
            $result[$row['item']] = $row['payload'] === null ? null : json_decode($row['payload'], true, 32, JSON_THROW_ON_ERROR);
        }
        return $result;
    }

    public function eraseInk(string $lesson): void
    {
        if (!preg_match('/^[a-f0-9]{32}$/D', $lesson)) throw new LogicException('Invalid lesson id');
        cl_query($this->db, "DELETE FROM `$this->table` WHERE bucket LIKE ? OR bucket LIKE ?", ['ink:' . $lesson . ':%', 'board-history:' . $lesson . ':%']);
    }

    public function tx(callable $work): mixed
    {
        $this->db->beginTransaction();
        try {
            $result = $work();
            $this->db->commit();
            return $result;
        } catch (Throwable $error) {
            if ($this->db->inTransaction()) $this->db->rollBack();
            throw $error;
        }
    }
}

function cl_store(): ClassroomStore
{
    $ctx = cl_context();
    return new ClassroomStore($ctx['pdo'], $ctx['store']);
}
