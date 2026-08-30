from __future__ import annotations

from cst_agent_workbench.agent.conversation_memory import (
    apply_constraints,
    extract_pending_questions,
    should_replace_user_goal,
)
from cst_agent_workbench.agent.memory import StructuredMemory, project_scope_from_path


def test_short_explicit_goal_can_replace_long_old_goal():
    old = "请创建天线、运行求解、持续优化直到满足指标并生成完整报告"

    assert should_replace_user_goal("停止优化，只导出结果", old) is True


def test_follow_up_question_does_not_erase_canonical_goal():
    assert should_replace_user_goal("为什么效果不好？", "优化贴片天线") is False


def test_explicit_constraints_are_added_and_scoped():
    memory = StructuredMemory()
    scope = project_scope_from_path("D:/projects/a.cst")

    delta = apply_constraints(
        memory,
        user_message="模型必须使用 microstrip feed；不要把任何缓存放到 C 盘。",
        project_scope=scope,
    )

    assert len(delta["added"]) == 2
    assert memory.conversation.constraints == [
        "模型必须使用 microstrip feed",
        "不要把任何缓存放到 C 盘",
    ]
    assert all(item["project_scope"] == scope for item in memory.conversation.constraint_records)


def test_constraints_from_other_project_are_not_projected():
    memory = StructuredMemory()
    scope_a = project_scope_from_path("D:/projects/a.cst")
    scope_b = project_scope_from_path("D:/projects/b.cst")
    apply_constraints(memory, user_message="必须使用 FR4", project_scope=scope_a)
    apply_constraints(memory, user_message="必须使用 Rogers5880", project_scope=scope_b)

    assert memory.conversation.constraints == ["必须使用 Rogers5880"]
    assert {item["text"] for item in memory.conversation.constraint_records} == {
        "必须使用 FR4",
        "必须使用 Rogers5880",
    }


def test_clear_current_project_constraints_preserves_other_project():
    memory = StructuredMemory()
    scope_a = project_scope_from_path("D:/projects/a.cst")
    scope_b = project_scope_from_path("D:/projects/b.cst")
    apply_constraints(memory, user_message="必须使用 FR4", project_scope=scope_a)
    apply_constraints(memory, user_message="必须使用 Rogers5880", project_scope=scope_b)

    delta = apply_constraints(memory, user_message="取消所有约束", project_scope=scope_b)

    assert delta["clear_all"] is True
    assert memory.conversation.constraints == []
    assert [item["text"] for item in memory.conversation.constraint_records] == ["必须使用 FR4"]


def test_pending_questions_only_capture_explicit_user_requests():
    response = "我已经分析完成。请确认是否允许我运行求解？结果可能需要几分钟。"

    assert extract_pending_questions(response) == ["请确认是否允许我运行求解？"]
