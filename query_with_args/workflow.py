from dataclasses import dataclass

from temporalio import workflow


@dataclass
class JoinInput:
    """Input identifying a user and the group they want to join."""
    user_id: str
    group_id: str


@workflow.defn
class GroupWorkflow:
    """An "entity"-style workflow holding group membership state.

    It never completes on its own: it waits forever so that callers can keep
    signalling new members in and querying the current state out.
    """

    def __init__(self) -> None:
        self.members: list[JoinInput] = []

    @workflow.run
    async def run(self) -> None:
        """Keep the workflow running to accept signals and serve queries."""
        await workflow.wait_condition(lambda: False)

    @workflow.signal
    def join(self, join_input: JoinInput) -> None:
        """Add a member to the group membership state."""
        workflow.logger.info(
            "User %s joined group %s", join_input.user_id, join_input.group_id
        )
        self.members.append(join_input)

    @workflow.query(name="members")
    def members_for_group(self, group_id: str) -> list[str]:
        """Return the user IDs of members in the specified group."""
        return [
            member.user_id
            for member in self.members
            if member.group_id == group_id
        ]
