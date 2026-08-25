 # Hall Agent 固定展厅保障表改造方案

  ## Summary

  把当前“动态记录与提醒 Agent”收敛为展厅保障场景专用 Agent：新库使用 data/hall_agent.db，每个单聊或群聊 scope 首次交互时自动拥有且只面向模型暴露一张固定格式表：“外部客户到访登记与接待报备表”。模型不
  再能创建/修改/归档表，也不能创建/修改/取消手工提醒或配置提醒策略；仅保留记录管理和提醒查询。所有新建/确认/更新时间有效的正式记录，由系统自动生成“到访开始前 2 小时”提醒。

  ## Key Changes

  - 新增固定表定义模块，例如 src/domain/fixed_reception_table.py：
      - 表名：外部客户到访登记与接待报备表
      - 字段采用参考库中 TBL_06455d049af0487b 的 active 字段版本：reception_location、visit_date、visit_start_time、visit_end_time、visiting_unit、main_visitor、visitor_count、host_department、
        exhibition_hall、meeting_room、supporting_materials、other_requirements、exhibition_status、internal_hosts
      - time_config 固定为 Asia/Shanghai，开始锚点为 visit_date + visit_start_time，结束锚点为 visit_date + visit_end_time
  - 新增 scope 级自动建表能力：
      - TableService.ensure_fixed_table(actor) 查找当前 scope 的固定表；不存在则创建，creation_policy="confirm"
      - 创建固定表后同时写入固定系统策略：relative_to_record / schedule_start / offset_minutes=-120
      - build_agent_deps 或 runtime prompt 构建前调用该方法，保证首次使用即有表
  - 收紧模型工具与提示词：
      - 从 record-management capability 移除 create_record_table、update_record_table、archive_record_table
      - 保留 list_record_tables、get_record_table、create_record、confirm_record、list_records、get_record、update_record、cancel_record
      - 从 reminder-management capability 移除 set_table_reminder_policy、clear_table_reminder_policy、create_reminder、update_reminder、cancel_reminder
      - 保留 list_reminders、get_reminder
      - 重写 system prompt：不再描述动态建表和手工提醒，只描述固定接待报备表、字段录入、冲突检查、记录确认、2 小时系统提醒
  - 保持服务层大体兼容：
      - 先不在 ReminderService 强制拒绝手工提醒/策略写接口，只是不暴露给模型
      - RecordService 继续通过 reconcile_record 在记录确认、修改、取消时联动提醒
      - README、测试描述、默认数据库路径从 reception/hall-agent 语义更新为 hall-agent 语义

  ## Test Plan

  - 单元测试：首次构建 deps/runtime prompt 后，当前 scope 自动出现唯一固定表，字段、必填项、枚举、时间锚点与选定模板一致。
  - 工具暴露测试：Agent capabilities 中不包含建表、改表、归档表、设置/清除策略、创建/更新/取消提醒工具。
  - 记录提醒测试：创建并确认一条未来到访记录后，只生成 1 条 table_policy 提醒，时间为到访开始前 120 分钟；修改到访时间后同一提醒重算；取消记录后提醒取消。
  - Prompt 测试：无表时不再提示“是否创建新表”，而是展示固定表摘要；用户请求手工提醒或改提醒策略时，Agent 应说明当前仅支持系统固定 2 小时提醒。
  - 回归测试：scope 隔离、记录草稿/确认、字段校验、查询过滤、调度器投递逻辑继续通过。

  ## Assumptions

  - data/reception_agent.db 只作为字段参考，不迁移旧表和旧记录；新开发使用 data/hall_agent.db。
  - 固定表使用 TBL_06455d049af0487b 的 active 字段，忽略其中 deprecated 的 internal_host。
  - “手工提醒等权限先关掉”按模型工具层关闭写能力处理，服务层暂不硬拒绝，便于后续后台或测试复用。
  - 2 小时提醒只对新库中新建、确认或更新后的 active 记录生效；不会处理参考库里的历史数据。
