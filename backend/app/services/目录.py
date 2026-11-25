from app.db import 连接


def 总览() -> dict:
    conn = 连接()
    try:
        def c(sql: str) -> int:
            return conn.execute(sql).fetchone()[0]

        return {
            "开关版本": c("SELECT COUNT(*) FROM 开关版本"),
            "分桶规": c("SELECT COUNT(*) FROM 分桶规 WHERE 状态='生效'"),
            "下发票": c("SELECT COUNT(*) FROM 下发票"),
            "已下发": c("SELECT COUNT(*) FROM 下发票 WHERE 状态='已下发'"),
            "缺计划票": c("SELECT COUNT(*) FROM 下发票 WHERE 状态='已下发' AND COALESCE(缺计划,0)=1"),
            "计划行": c("SELECT COUNT(*) FROM 回读计划行"),
            "可晋升": c(
                """SELECT COUNT(*) FROM 下发票 t WHERE t.状态='已下发'
                   AND COALESCE(t.缺计划,0)=0
                   AND EXISTS (SELECT 1 FROM 回读计划行 p WHERE p.票号=t.票号)
                   AND NOT EXISTS (
                     SELECT 1 FROM 回读计划行 p WHERE p.票号=t.票号 AND p.必需=1 AND p.状态<>'已确认'
                   )"""
            ),
            "回读证": c("SELECT COUNT(*) FROM 回读证"),
            "确认回读": c("SELECT COUNT(*) FROM 回读证 WHERE 结果='确认'"),
            "命运条": c("SELECT COUNT(*) FROM 命运账"),
            "最近贯验通过": bool(
                (conn.execute(
                    "SELECT 是否通过 FROM 轨贯验 ORDER BY 编号 DESC LIMIT 1"
                ).fetchone() or [0])[0]
            ),
        }
    finally:
        conn.close()
