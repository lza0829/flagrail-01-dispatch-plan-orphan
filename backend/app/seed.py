from datetime import datetime, timezone

from app.db import 连接

建表 = """
CREATE TABLE IF NOT EXISTS 站规 (
  键 TEXT PRIMARY KEY,
  值 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 开关版本 (
  版本键 TEXT PRIMARY KEY,
  开关键 TEXT NOT NULL,
  版本号 INTEGER NOT NULL,
  载荷 TEXT NOT NULL DEFAULT '{}',
  状态 TEXT NOT NULL DEFAULT '已入轨',
  入轨时 TEXT NOT NULL,
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 分桶规 (
  桶键 TEXT PRIMARY KEY,
  开关键 TEXT NOT NULL,
  桶类型 TEXT NOT NULL,
  规则 TEXT NOT NULL DEFAULT '{}',
  状态 TEXT NOT NULL DEFAULT '生效',
  更新时 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 下发票 (
  票号 TEXT PRIMARY KEY,
  开关键 TEXT NOT NULL,
  版本键 TEXT NOT NULL,
  桶键 TEXT NOT NULL,
  状态 TEXT NOT NULL DEFAULT '待下发',
  下发时 TEXT NOT NULL DEFAULT '',
  世代 INTEGER NOT NULL DEFAULT 1,
  说明 TEXT NOT NULL DEFAULT '',
  缺计划 INTEGER NOT NULL DEFAULT 0,
  晋升状态 TEXT NOT NULL DEFAULT '未晋升',
  失败口径 TEXT NOT NULL DEFAULT '',
  故障说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 回读计划行 (
  票号 TEXT NOT NULL,
  边缘键 TEXT NOT NULL,
  必需 INTEGER NOT NULL DEFAULT 1,
  状态 TEXT NOT NULL DEFAULT '待回读',
  所见版本键 TEXT NOT NULL DEFAULT '',
  回读证号 TEXT NOT NULL DEFAULT '',
  更新时 TEXT NOT NULL DEFAULT '',
  说明 TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (票号, 边缘键)
);
CREATE INDEX IF NOT EXISTS idx_回读计划_票 ON 回读计划行(票号);
CREATE TABLE IF NOT EXISTS 故障开关 (
  键 TEXT PRIMARY KEY,
  启用 INTEGER NOT NULL DEFAULT 0,
  口径 TEXT NOT NULL DEFAULT '',
  剩余次数 INTEGER NOT NULL DEFAULT 1,
  更新时 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 回读证 (
  证号 TEXT PRIMARY KEY,
  票号 TEXT NOT NULL,
  边缘键 TEXT NOT NULL,
  所见版本键 TEXT NOT NULL DEFAULT '',
  结果 TEXT NOT NULL,
  回读时 TEXT NOT NULL,
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 命运账 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  对象类型 TEXT NOT NULL,
  对象键 TEXT NOT NULL,
  命运 TEXT NOT NULL,
  时刻 TEXT NOT NULL,
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 装载批次 (
  编号 TEXT PRIMARY KEY,
  时刻 TEXT NOT NULL,
  报告 TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS 轨贯验 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  时刻 TEXT NOT NULL,
  版本数 INTEGER NOT NULL,
  下发数 INTEGER NOT NULL,
  回读数 INTEGER NOT NULL,
  命运数 INTEGER NOT NULL,
  是否通过 INTEGER NOT NULL,
  说明 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 空桩 (
  名 TEXT PRIMARY KEY,
  状态 TEXT NOT NULL DEFAULT '空桩'
);
"""

# 旧库补列：CREATE TABLE IF NOT EXISTS 不会给已存在的表加列。
下发票补列 = {
    "缺计划": "INTEGER NOT NULL DEFAULT 0",
    "晋升状态": "TEXT NOT NULL DEFAULT '未晋升'",
    "失败口径": "TEXT NOT NULL DEFAULT ''",
    "故障说明": "TEXT NOT NULL DEFAULT ''",
}


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _补列(conn, 表: str, 列定义: dict) -> None:
    已有 = {r["name"] for r in conn.execute(f"PRAGMA table_info({表})").fetchall()}
    for 列, 类型 in 列定义.items():
        if 列 not in 已有:
            conn.execute(f"ALTER TABLE {表} ADD COLUMN {列} {类型}")


def _建回读计划别名(conn) -> None:
    """把计划表以「回读计划」之名再暴露一份（白盒/核验口径兼容）。

    仅当该名字未被任何关系占用时建可更新视图 + INSTEAD OF INSERT 触发器；
    若旧库已存在同名基表则保持原样，绝不因别名问题阻断启动。
    """
    try:
        row = conn.execute(
            "SELECT type FROM sqlite_master WHERE type IN ('table','view') AND name='回读计划'"
        ).fetchone()
        if row is not None:
            return  # 已有同名表或视图，不覆盖。
        conn.execute("CREATE VIEW 回读计划 AS SELECT * FROM 回读计划行")
        conn.executescript(
            """
            CREATE TRIGGER trg_回读计划_插入
            INSTEAD OF INSERT ON 回读计划
            BEGIN
              INSERT OR IGNORE INTO 回读计划行(票号, 边缘键, 必需, 状态, 所见版本键, 回读证号, 更新时, 说明)
              VALUES (
                NEW.票号, NEW.边缘键, COALESCE(NEW.必需, 1),
                COALESCE(NEW.状态, '待回读'), COALESCE(NEW.所见版本键, ''),
                COALESCE(NEW.回读证号, ''), COALESCE(NEW.更新时, ''), COALESCE(NEW.说明, '')
              );
            END;
            """
        )
        conn.commit()
    except Exception:
        # 别名纯属兼容层，任何异常都不应影响主结构与启动。
        try:
            conn.rollback()
        except Exception:
            pass


def 初始化库() -> None:
    conn = 连接()
    try:
        conn.executescript(建表)
        _补列(conn, "下发票", 下发票补列)
        _建回读计划别名(conn)
        # 在途有效下发唯一槽：同 版本+桶 至多一张「计划齐、未全量」的已下发票。
        # 作为并发双有效的库级兜底；若历史脏数据已重复则退化为普通索引，正确性由立即事务兜底。
        try:
            conn.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS 唯一在途有效下发
                   ON 下发票(版本键, 桶键)
                   WHERE 状态='已下发' AND 缺计划=0 AND 晋升状态='未晋升'"""
            )
        except Exception:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_下发票_版本桶 ON 下发票(版本键, 桶键)")
        if conn.execute("SELECT COUNT(*) AS c FROM 站规").fetchone()["c"] == 0:
            for k, v in {
                "站名": "灰度轨",
                "领域": "基础设施",
                "子方向": "中间件",
                "默认灰度比": "10",
                "回读齐套策略": "全边缘确认才可晋升",
                "计划默认边缘": "edge-a,edge-b",
                # 计划写失败的默认正确性口径：回退（票随计划回退，同成败）。
                # 另一合法口径为「悬挂」：票保持已下发但打缺计划标记并闸死回读/晋升。
                "计划失败口径": "回退",
                "骨架": "开关版本入轨→分桶灰度下发→回读确认→失效/回滚互证",
            }.items():
                conn.execute("INSERT INTO 站规(键, 值) VALUES (?, ?)", (k, v))
        else:
            conn.execute("INSERT OR IGNORE INTO 站规(键, 值) VALUES ('计划默认边缘', 'edge-a,edge-b')")
            conn.execute("INSERT OR IGNORE INTO 站规(键, 值) VALUES ('计划失败口径', '回退')")
        for name in [
            "分桶比例冲突", "名单与百分比互斥", "回读超时旁置", "半截下发回滚",
            "双版本同键并发", "回读偏差拒升全量", "环境桶隔离", "失效后幽灵回读",
            "下发票幂等键", "回滚窗互斥", "变体载荷契约", "边缘时钟漂移",
            "分桶重切", "全量晋升闸", "影子轨对照", "配置世代戳",
            "回读仲裁策略", "失效写闸", "跨环境串扰", "轨贯验三联",
        ]:
            conn.execute(
                "INSERT OR IGNORE INTO 空桩(名, 状态) VALUES (?, '空桩')", (name,)
            )
        # 本题空桩已落地为「下发回读计划同票」真实现，明确标记而非继续装空桩。
        conn.execute(
            "INSERT INTO 空桩(名, 状态) VALUES (?, '已落地') "
            "ON CONFLICT(名) DO UPDATE SET 状态='已落地'",
            ("下发回读计划同票",),
        )
        conn.commit()
    finally:
        conn.close()
