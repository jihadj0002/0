"""
Executor (P0-5): Deterministic Python tool runner.

Takes a plan (list of PlanStep) and runs each step sequentially.
No LLM calls — pure Python execution with timeout, retry, and permission checks.
"""
import logging
import time

from .context import ConversationContext, PlanStep
from .tools import ToolResult, ToolRegistry

logger = logging.getLogger(__name__)


class Executor:

    @staticmethod
    def execute(
        plan: list[PlanStep],
        context: ConversationContext,
    ) -> list[ToolResult]:
        """Run all plan steps sequentially, collecting results.

        Args:
            plan: List of PlanStep objects to execute
            context: Current conversation context

        Returns:
            List of ToolResult objects, one per step executed
        """
        results: list[ToolResult] = []
        if not plan:
            return results

        for step_index, step in enumerate(plan):
            tool = ToolRegistry.get(step.tool)
            if not tool:
                results.append(ToolResult.as_error(
                    f"Unknown tool: {step.tool}",
                    tool=step.tool,
                ))
                break

            # Check dependency satisfaction
            if step.depends_on:
                deps_satisfied = all(
                    i < len(results) and results[i].state == "success"
                    for i in step.depends_on
                )
                if not deps_satisfied:
                    results.append(ToolResult.as_error(
                        "Dependencies not satisfied",
                        tool=step.tool,
                    ))
                    break

            # Execute with retry
            result = Executor._execute_with_retry(
                step, context, step_index
            )
            results.append(result)

            # Handle error with fallback
            if result.state == "error" and step.fallback:
                fallback_step = PlanStep(
                    tool=step.fallback,
                    args=step.args,
                    timeout_ms=step.timeout_ms,
                )
                fallback_result = Executor._execute_with_retry(
                    fallback_step, context, step_index
                )
                results.append(fallback_result)
                if fallback_result.state == "error":
                    break
            elif result.state == "error":
                break

            # Propagate data to subsequent steps
            if result.data and step_index + 1 < len(plan):
                Executor._propagate(result.data, step_index, plan)

        return results

    @staticmethod
    def _execute_with_retry(
        step: PlanStep,
        context: ConversationContext,
        step_index: int,
    ) -> ToolResult:
        """Execute a single plan step with retries."""
        last_error = None

        # Permission check (P1-2): every tool call verifies role permission first
        try:
            from .policy import PermissionChecker
            allowed, reason = PermissionChecker.can_execute(context.user, step.tool)
            if not allowed:
                logger.info(
                    "Permission denied step=%d tool=%s user=%s: %s",
                    step_index, step.tool, context.user.pk, reason,
                )
                denied = ToolResult.permission_denied(tool=step.tool)
                Executor._write_audit(step, context, denied, "permission_denied")
                return denied
        except Exception as exc:
            logger.warning("Permission check failed step=%d: %s", step_index, exc)

        for attempt in range(max(1, step.retry_count)):
            try:
                t0 = time.time()
                result = ToolRegistry.execute(
                    step.tool,
                    step.args,
                    context.user,
                    context.conversation,
                )
                result.execution_time_ms = int((time.time() - t0) * 1000)

                Executor._write_audit(step, context, result, result.state)

                if result.state == "success":
                    logger.debug(
                        "Step %d tool=%s attempt=%d OK (%dms)",
                        step_index, step.tool, attempt + 1, result.execution_time_ms,
                    )
                    return result

                last_error = result
                if result.state != "error":
                    return result  # non-retryable states

            except Exception as exc:
                last_error = ToolResult.as_error(str(exc), tool=step.tool)
                logger.warning(
                    "Step %d tool=%s attempt=%d failed: %s",
                    step_index, step.tool, attempt + 1, exc,
                )

            if attempt < step.retry_count - 1:
                time.sleep(0.1 * (attempt + 1))  # progressive backoff

        return last_error or ToolResult.as_error("Max retries exceeded", tool=step.tool)

    @staticmethod
    def _write_audit(step: PlanStep, context: ConversationContext, result: ToolResult, state: str):
        """Persist an AuditLog row for a tool execution (P1-3)."""
        try:
            from back.models import AuditLog
            from .policy import PermissionChecker

            summary = result.error or ""
            if result.data and isinstance(result.data, dict):
                summary = str(result.data)[:500]

            AuditLog.objects.create(
                user=context.user,
                conversation=context.conversation,
                tool_name=step.tool,
                arguments=step.args or {},
                result_state=state,
                result_summary=summary,
                execution_time_ms=result.execution_time_ms,
                actor_role=PermissionChecker.get_user_role(context.user),
            )
        except Exception as exc:
            logger.warning("AuditLog write failed tool=%s: %s", step.tool, exc)

    @staticmethod
    def _propagate(data: dict, from_index: int, plan: list[PlanStep]):
        """Pass result data to subsequent steps (e.g., pid from search → details)."""
        if not data:
            return

        # If search_products returned products, pass the first pid to subsequent steps
        products = data.get("products") or data.get("data", {}).get("products")
        if products and isinstance(products, list) and len(products) > 0:
            first_pid = products[0].get("pid")
            if first_pid:
                for i in range(from_index + 1, min(from_index + 4, len(plan))):
                    step = plan[i]
                    if step.tool in ("get_product_details", "send_images"):
                        if not step.args.get("pid"):
                            step.args["pid"] = first_pid
                    if step.tool == "create_order":
                        items = step.args.get("items", [])
                        if not items:
                            step.args["items"] = [{"pid": first_pid, "quantity": 1}]

        # Propagate customer info from update_customer
        updated = data.get("updated") or data.get("data", {}).get("updated")
        if updated and isinstance(updated, list):
            for i in range(from_index + 1, len(plan)):
                step = plan[i]
                if step.tool == "create_order":
                    for field in updated:
                        if field not in step.args:
                            step.args[field] = data.get(field) or data.get("data", {}).get(field)
