"""
记忆管理器（Memory Manager）
- 统一管理三层记忆的读写
- 协调工作记忆→短期记忆→长期记忆的数据流转
- 提供给Agent核心循环的统一接口
- 集成增强检索器，提供模糊匹配和同义词扩展
"""

from typing import Any, Dict, List, Optional

from .enhanced_retriever import EnhancedRetriever
from .long_term import LongTermMemory
from .short_term import ShortTermMemory
from .working import StepRecord, WorkingMemory


class MemoryManager:
    """
    记忆管理器：三层记忆的统一入口
    
    用法：
        mm = MemoryManager(
            working=WorkingMemory(),
            short_term=ShortTermMemory("data/short_term.db"),
            long_term=LongTermMemory("data/long_term/")
        )
        
        # 任务开始
        mm.start_task(task_id, goal, sub_tasks)
        
        # 执行过程中
        mm.add_step(StepRecord(...))
        mm.update_device_state({"screen": "login"})
        prompt = mm.get_full_context_prompt()  # 给LLM
        
        # 任务完成 → 自动流转到短期+长期记忆
        task_log = mm.end_task()
        mm.save_to_short_term(task_log)
        mm.save_to_long_term(reflection_result)
    """

    def __init__(self, working: WorkingMemory = None,
                 short_term: ShortTermMemory = None,
                 long_term: LongTermMemory = None):
        self.working = working or WorkingMemory()
        self.short_term = short_term or ShortTermMemory()
        self.long_term = long_term or LongTermMemory()
        self.retriever = EnhancedRetriever()

    # ── 任务生命周期代理 ──

    def start_task(self, task_id: str, goal: str, sub_tasks: list[str],
                   environment: dict = None):
        """开始新任务"""
        self.working.start_task(task_id, goal, sub_tasks, environment)

    def end_task(self) -> dict:
        """结束任务，返回完整日志"""
        return self.working.end_task()

    # ── 工作记忆代理 ──

    def add_step(self, step: StepRecord):
        """添加执行步骤"""
        self.working.add_step(step)

    def update_device_state(self, state: dict):
        """更新设备状态"""
        self.working.update_device_state(state)

    def update_environment(self, env: dict):
        """更新环境信息"""
        self.working.update_environment(env)

    # ── 短期记忆写入 ──

    def save_to_short_term(self, task_log: dict):
        """将任务日志保存到短期记忆"""
        self.short_term.record_task(task_log)

    def update_failure_analysis(self, task_id: str, step_id: str,
                                error_type: str, root_cause: str,
                                solution: str):
        """更新短期记忆中的失败分析"""
        self.short_term.update_failure_analysis(
            task_id, step_id, error_type, root_cause, solution
        )

    # ── 长期记忆写入 ──

    def learn_from_reflection(self, reflection_result: dict):
        """从反思结果学习到长期记忆"""
        self.long_term.learn_from_reflection(reflection_result)

    # ── 查询接口 ──

    def find_similar_failures(self, error_description: str,
                              limit: int = 5) -> list[dict]:
        """查找历史类似失败（短期记忆）"""
        return self.short_term.find_similar_failures(error_description, limit)

    def query_strategies(self, task_type: str) -> list:
        """查询成功策略（长期记忆）"""
        return self.long_term.query_strategies(task_type)

    def query_failures(self, task_type: str = None,
                       error_type: str = None) -> list:
        """查询已知失败模式（长期记忆）"""
        return self.long_term.query_failures(task_type, error_type)

    # ── 增强检索接口 ──

    def enhanced_search(self, query: str, threshold: float = 0.6) -> list[dict]:
        """
        增强检索：模糊匹配 + 同义词扩展
        
        Args:
            query: 查询文本
            threshold: 相似度阈值
        
        Returns:
            匹配结果列表
        """
        # 从短期记忆获取候选
        candidates = self.short_term.get_all_records()
        
        # 使用增强检索器
        return self.retriever.fuzzy_search(query, candidates, threshold)

    def context_aware_search(self, query: str, context: dict) -> list[dict]:
        """
        上下文感知检索
        
        Args:
            query: 查询文本
            context: 上下文信息（如 app, device 等）
        
        Returns:
            加权后的匹配结果
        """
        candidates = self.short_term.get_all_records()
        return self.retriever.context_weighted_search(query, candidates, context)

    def expand_query_synonyms(self, query: str) -> list[str]:
        """
        扩展查询的同义词
        
        Args:
            query: 查询文本
        
        Returns:
            同义词列表
        """
        return self.retriever.expand_synonyms(query)

    # ── 给LLM的完整上下文 ──

    def get_full_context_prompt(self, device_model: str = None) -> str:
        """
        组装给LLM的完整上下文prompt
        
        包含三层记忆的相关信息：
        - 工作记忆：当前任务进度和状态
        - 短期记忆：近期类似场景的经验
        - 长期记忆：领域知识和已知模式
        """
        parts = []

        # 工作记忆（最重要，放在最前）
        working_prompt = self.working.get_context_prompt()
        if working_prompt:
            parts.append(working_prompt)

        # 长期记忆的相关知识
        if self.working.context:
            goal = self.working.context.goal
            ltm_prompt = self.long_term.get_relevant_knowledge_prompt(
                goal, device_model
            )
            if ltm_prompt:
                parts.append("\n" + ltm_prompt)

            # 短期记忆：最近的类似失败
            recent_stats = self.short_term.get_recent_stats(days=7)
            if recent_stats["total_tasks"] > 0:
                parts.append("\n## 📊 最近执行经验")
                parts.append(f"最近7天: {recent_stats['total_tasks']}次任务, "
                             f"成功率{recent_stats['success_rate']:.0%}, "
                             f"平均重试{recent_stats['avg_retries']}次")

                # 如果有失败步骤，查询类似历史
                failed_steps = self.working.get_failed_steps()
                if failed_steps:
                    for step in failed_steps[-2:]:  # 最近的2个失败
                        similar = self.find_similar_failures(step.observation, limit=3)
                        if similar:
                            parts.append(f"\n### 类似历史失败: [{step.step_id}]")
                            for s in similar[:2]:
                                if s.get("root_cause"):
                                    parts.append(f"- 根因: {s['root_cause']} → 解决: {s.get('solution', '暂无')}")

        return "\n".join(parts)

    # ── 维护 ──

    def decay_short_term(self, max_age_days: int = 30):
        """清理短期记忆过期数据"""
        self.short_term.decay(max_age_days)

    def close(self):
        """关闭所有记忆连接"""
        self.short_term.close()