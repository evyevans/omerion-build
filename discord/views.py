"""Discord UI views for the Omerion bot.

HITLView: Approve / Reject / Edit Draft buttons on founder review cards.
EditModal: text input modal for editing a draft before approving.
"""
from __future__ import annotations

import logging

import discord

log = logging.getLogger("omerion.discord.views")


class EditModal(discord.ui.Modal, title="Edit Draft"):
    revised_copy = discord.ui.TextInput(
        label="Revised copy",
        style=discord.TextStyle.paragraph,
        placeholder="Paste the corrected draft here…",
        required=True,
        max_length=4000,
    )

    def __init__(self, view: "HITLView"):
        super().__init__()
        self._hitl_view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            result = await self._hitl_view.client.resolve_hitl(
                review_id=self._hitl_view.review_id,
                token=self._hitl_view.approve_token,
                decision="edited",
                new_body=self.revised_copy.value,
            )
            await interaction.response.edit_message(
                content=(
                    f"✏️ **Draft updated** — {self._hitl_view.subject}\n"
                    f"Revised copy saved. Thread resumed."
                ),
                view=None,
            )
            log.info("hitl_edited", review_id=self._hitl_view.review_id)
        except Exception as exc:
            log.error("hitl_edit_error", review_id=self._hitl_view.review_id, error=str(exc))
            await interaction.response.send_message(
                f"❌ Failed to save edit: {exc}", ephemeral=True
            )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.error("edit_modal_error", error=str(error))
        await interaction.response.send_message("❌ Something went wrong.", ephemeral=True)


class HITLView(discord.ui.View):
    """Persistent approve/reject/edit view for HITL review cards.

    timeout=None keeps buttons active across bot restarts.
    """

    def __init__(
        self,
        review_id: str,
        agent_name: str,
        subject: str,
        approve_token: str,
        reject_token: str,
        client,  # OmerionClient — typed loosely to avoid circular import
    ):
        super().__init__(timeout=None)
        self.review_id = review_id
        self.agent_name = agent_name
        self.subject = subject
        self.approve_token = approve_token
        self.reject_token = reject_token
        self.client = client

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success, custom_id="hitl_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            await self.client.resolve_hitl(
                review_id=self.review_id,
                token=self.approve_token,
                decision="approved",
            )
            await interaction.response.edit_message(
                content=f"✅ **Approved** — _{self.subject}_\n*Agent {self.agent_name} will continue.*",
                view=None,
            )
            log.info("hitl_approved", review_id=self.review_id)
        except Exception as exc:
            log.error("hitl_approve_error", review_id=self.review_id, error=str(exc))
            await interaction.response.send_message(
                f"❌ Approval failed: {exc}", ephemeral=True
            )

    @discord.ui.button(label="❌ Reject", style=discord.ButtonStyle.danger, custom_id="hitl_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            await self.client.resolve_hitl(
                review_id=self.review_id,
                token=self.reject_token,
                decision="rejected",
            )
            await interaction.response.edit_message(
                content=f"❌ **Rejected** — _{self.subject}_\n*Draft discarded.*",
                view=None,
            )
            log.info("hitl_rejected", review_id=self.review_id)
        except Exception as exc:
            log.error("hitl_reject_error", review_id=self.review_id, error=str(exc))
            await interaction.response.send_message(
                f"❌ Rejection failed: {exc}", ephemeral=True
            )

    @discord.ui.button(label="✏️ Edit Draft", style=discord.ButtonStyle.secondary, custom_id="hitl_edit")
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(EditModal(view=self))
