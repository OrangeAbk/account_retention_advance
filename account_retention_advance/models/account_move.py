from odoo import api, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    retention_percentage = fields.Float(
        "Retention (%)",
        default=lambda self: self.env.company.default_retention_percentage or 0.0,
        digits=(16, 2),)

    retention_amount = fields.Monetary(string="Retention Amount",compute="_compute_retention_advance",
        store=True,currency_field="currency_id",)

    advance_payment_id = fields.Many2one("account.payment",string="Advance Payment",check_company=True,
        domain="[('partner_id', '=', commercial_partner_id), "
               "('company_id', '=', company_id), "
               "('partner_type', '=', 'customer'), "
               "('state', 'not in', ('draft', 'canceled', 'rejected'))]" )

    advance_recovery_method = fields.Selection([
            ("percentage", "Percentage"),
            ("fixed", "Fixed Amount"),
        ],
        string="Recovery Method", default="percentage" )

    advance_recovery_percentage = fields.Float(string="Recovery (%)",default=20.0,digits=(16, 2),)

    advance_recovery_fixed = fields.Monetary( string="Fixed Recovery Amount",currency_field="currency_id", )

    advance_recovery_amount = fields.Monetary( string="Advance Recovery",compute="_compute_retention_advance",store=True,currency_field="currency_id",)

    amount_payable_now = fields.Monetary(string="Amount Payable Now",compute="_compute_retention_advance",store=True,currency_field="currency_id", )

    advance_recovery_remaining = fields.Monetary(string="Advance Balance",compute="_compute_advance_remaining",
        store=True, currency_field="currency_id", )
    retention_due_date = fields.Date(
        string="Retention Due Date",help="Date when the retained amount becomes payable.",)
    retention_move_id = fields.Many2one( "account.move",string="Retention Journal Entry",copy=False, readonly=True,check_company=True,)
    retention_split_done = fields.Boolean(string="Retention Split Done",copy=False, default=False,readonly=True, )



    @api.depends(
        "invoice_line_ids.price_subtotal",
        "invoice_line_ids.price_total",
        "invoice_line_ids.quantity",
        "invoice_line_ids.price_unit",
        "invoice_line_ids.discount",
        "amount_untaxed",
        "amount_tax",
        "amount_total",
        "retention_percentage",
        "advance_payment_id",
        "advance_recovery_method",
        "advance_recovery_percentage",
        "advance_recovery_fixed",
        "currency_id",
        "move_type",
    )
    def _compute_retention_advance(self):
        for move in self:
            lines = move.invoice_line_ids.filtered(
                lambda l: l.display_type not in ("line_section", "line_note")
            )
            work_amount = sum(lines.mapped("price_subtotal"))

            retention = work_amount * (move.retention_percentage or 0.0) / 100.0

            if move.advance_recovery_method == "percentage":
                recovery = work_amount * (move.advance_recovery_percentage or 0.0) / 100.0
            else:
                recovery = move.advance_recovery_fixed or 0.0

            remaining = move._get_advance_remaining()
            recovery = min(recovery, remaining)

            move.retention_amount = move.currency_id.round(retention)
            move.advance_recovery_amount = move.currency_id.round(recovery)

            total = sum(lines.mapped("price_total"))
            move.amount_payable_now = (
                total
                - move.retention_amount
                - move.advance_recovery_amount
            )

    @api.depends("advance_payment_id", "advance_recovery_amount")
    def _compute_advance_remaining(self):
        for move in self:
            move.advance_recovery_remaining = move._get_advance_remaining()

    def _get_advance_remaining(self):
        self.ensure_one()
        if not self.advance_payment_id:
            return 0.0

        already = sum(
            self.search([
                ("advance_payment_id", "=", self.advance_payment_id.id),
                ("state", "=", "posted"),
                ("id", "!=", self.id),
            ]).mapped("advance_recovery_amount")
        )
        if self.state == "posted" and self.id:
            already += self.advance_recovery_amount

        return max(self.advance_payment_id.amount - already, 0.0)

    @api.onchange("advance_recovery_method")
    def _onchange_advance_recovery_method(self):
        if self.advance_recovery_method == "percentage":
            self.advance_recovery_fixed = 0.0
        else:
            self.advance_recovery_percentage = 0.0

    @api.constrains("advance_recovery_amount", "advance_payment_id")
    def _check_advance_recovery(self):
        for move in self:
            if move.advance_recovery_amount < 0:
                raise UserError("Advance recovery cannot be negative.")
            if move.advance_payment_id and move.advance_recovery_amount > \
                    move.advance_payment_id.amount:
                raise UserError(
                    "Advance recovery cannot exceed the advance amount."
                )



    def _post(self, soft=True):
        res = super()._post(soft=soft)
        for move in self.filtered(
                lambda m: m.move_type in ("out_invoice", "out_refund")
                          and m.state == "posted"
                          and not m.retention_split_done
        ):
            move._split_receivable_for_retention_advance()
        return res

    def button_draft(self):
        res = super().button_draft()
        for move in self:
            if not move.retention_split_done:
                continue

            ctx = {
                "check_move_validity": False,
                "skip_invoice_sync": True,
                "dynamic_unlink": True,
                "skip_retention_split": True,
            }

            move.line_ids.filtered(
                lambda l: l.name in ("Retention", "Advance Recovery")
            ).with_context(**ctx).unlink()
            company = move.company_id
            partner = move.commercial_partner_id
            partner_ar = partner.with_context(
                allowed_company_ids=[company.id], company_id=company.id
            ).property_account_receivable_id
            if partner_ar:
                ar_line = move.line_ids.filtered(
                    lambda l: l.account_id.account_type == "asset_receivable"
                              and l.display_type == "payment_term"
                              and l.name not in ("Retention", "Advance Recovery")
                )[:1]
                if ar_line and ar_line.account_id != partner_ar:
                    ar_line.with_context(**ctx).write({
                        "account_id": partner_ar.id,
                    })

            move.with_context(skip_invoice_sync=True).write({
                "retention_split_done": False,
                "retention_move_id": False,
            })

        return res

    def _split_receivable_for_retention_advance(self):
        self.ensure_one()

        if self.retention_split_done:
            return

        retention = self.currency_id.round(self.retention_amount or 0.0)
        recovery = self.currency_id.round(self.advance_recovery_amount or 0.0)
        total_deduction = retention + recovery
        if not total_deduction:
            return

        company = self.company_id
        partner = self.commercial_partner_id
        sign = -1 if self.move_type == "out_refund" else 1

        ctx = dict(
            check_move_validity=False,
            dynamic_unlink=True,
            skip_invoice_sync=True,
            skip_retention_split=True,
        )

        self.line_ids.filtered(
            lambda l: l.name in ("Retention", "Advance Recovery")
        ).with_context(**ctx).unlink()

        ar_line = self.line_ids.filtered(
            lambda l: l.account_id.account_type == "asset_receivable"
                      and l.display_type == "payment_term"
        )[:1]
        if not ar_line:
            raise UserError("Could not find the receivable line on the invoice.")

        ar_maturity = ar_line.date_maturity or self.invoice_date_due or self.invoice_date
        if sign > 0:
            new_debit = ar_line.debit - total_deduction
            if new_debit < 0:
                raise UserError("Deductions exceed the invoice receivable.")
            ar_line.with_context(**ctx).write({
                "debit": new_debit,
                "date_maturity": ar_maturity,
            })
        else:
            new_credit = ar_line.credit - total_deduction
            if new_credit < 0:
                raise UserError("Deductions exceed the credit note receivable.")
            ar_line.with_context(**ctx).write({
                "credit": new_credit,
                "date_maturity": ar_maturity,
            })

        if retention:
            retention_account = company.retention_receivable_account_id
            if not retention_account:
                raise UserError("Please set the Retention Receivable Account in Settings.")
            self.env["account.move.line"].with_context(**ctx).create({
                "move_id": self.id,
                "account_id": retention_account.id,
                "partner_id": partner.id,
                "name": "Retention",
                "debit": retention if sign > 0 else 0.0,
                "credit": retention if sign < 0 else 0.0,
                "currency_id": self.currency_id.id,
                "company_id": company.id,
            })

        if recovery:
            advance_account = company.advance_liability_account_id
            if not advance_account:
                raise UserError("Please set the Advance Liability Account in Settings.")
            self.env["account.move.line"].with_context(**ctx).create({
                "move_id": self.id,
                "account_id": advance_account.id,
                "partner_id": partner.id,
                "name": "Advance Recovery",
                # "display_type": "payment_term",
                "debit": recovery if sign > 0 else 0.0,
                "credit": recovery if sign < 0 else 0.0,
                "currency_id": self.currency_id.id,
                "company_id": company.id,
            })

        self.with_context(skip_invoice_sync=True).write({
            "retention_split_done": True,
        })

        self.invalidate_recordset(["amount_residual", "amount_total_signed"])
        ar_line.invalidate_recordset(["amount_residual", "amount_residual_currency"])

