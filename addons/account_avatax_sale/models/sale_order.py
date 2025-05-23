from odoo import models, _
from odoo.tools.misc import formatLang


class SaleOrder(models.Model):
    _name = 'sale.order'
    _inherit = ['sale.order', 'account.avatax']

    def _action_confirm(self):
        self.button_update_avatax()
        return super()._action_confirm()

    def action_quotation_send(self):
        self.button_update_avatax()
        return super().action_quotation_send()

    def button_update_avatax(self):
        mapped_taxes, _ = self.filtered(lambda m: m.fiscal_position_id.is_avatax)._map_avatax(False)
        to_flush = self.env['sale.order.line']
        for line, detail in mapped_taxes.items():
            line.tax_id = detail['tax_ids']
            to_flush += line

        # Trigger field computation due to changing the tax id. Do
        # this here because we will manually change the taxes.
        to_flush.flush_recordset(['price_tax', 'price_subtotal', 'price_total'])

        for line, detail in mapped_taxes.items():
            line.price_tax = detail['tax_amount']
            line.price_subtotal = detail['total']
            line.price_total = detail['tax_amount'] + detail['total']

    def _compute_tax_totals(self):
        """ This overrides the standard values which come from
        account.tax. The percentage (amount field) on account.tax
        won't be correct in case of (partial) exemptions. As always we
        should rely purely on the values Avatax returns, not the
        values Odoo computes. This will create a single tax group
        using the amount_* fields on the order which come from Avatax.
        """
        res = super()._compute_tax_totals()
        group_name = _('Untaxed Amount')
        for order in self.filtered(lambda so: so.fiscal_position_id.is_avatax):
            currency = order.currency_id
            tax_totals = order.tax_totals

            tax_totals['groups_by_subtotal'] = {
                group_name: [{
                    'tax_group_name': _('Taxes'),
                    'tax_group_amount': order.amount_tax,
                    'tax_group_base_amount': order.amount_untaxed,
                    'formatted_tax_group_amount': formatLang(self.env, order.amount_tax, currency_obj=currency),
                    'formatted_tax_group_base_amount': formatLang(self.env, order.amount_untaxed, currency_obj=currency),
                    'tax_group_id': 1,
                    'group_key': 1,
                }]
            }
            tax_totals['subtotals'] = [{
                'name': group_name,
                'amount': order.amount_untaxed,
                'formatted_amount': formatLang(self.env, order.amount_untaxed, currency_obj=currency),
            }]
            tax_totals['amount_total'] = order.amount_total
            tax_totals['formatted_amount_total'] = formatLang(self.env, order.amount_total, currency_obj=currency)

            order.tax_totals = tax_totals

        return res

    def _compute_amounts(self):
        """ This overrides the standard values for orders using Avatax.
        The round_globally option doesn't work with Avatax hence the
        tax (amount_tax field) on sale.order won't be correct in
        case of (partial) exemptions. As always we should rely purely
        on the values Avatax returns, not the values Odoo computes.
        """
        avatax_orders = self.filtered(lambda so: so.fiscal_position_id.is_avatax)
        for order in avatax_orders:
            order_lines = order.order_line.filtered(lambda x: not x.display_type)
            order.amount_untaxed = sum(order_lines.mapped('price_subtotal'))
            order.amount_tax = sum(order_lines.mapped('price_tax'))
            order.amount_total = order.amount_untaxed + order.amount_tax
        super(SaleOrder, self - avatax_orders)._compute_amounts()

    def _get_avatax_invoice_lines(self):
        return [
            self._get_avatax_invoice_line(
                product=line.product_id,
                price_subtotal=line.price_subtotal,
                quantity=line.product_uom_qty,
                line_id='%s,%s' % (line._name, line.id),
            )
            for line in self.order_line.filtered(lambda l: not l.display_type)
        ]

    def _get_avatax_dates(self):
        return (self.date_order, self.date_order)

    def _get_avatax_document_type(self):
        return 'SalesOrder'

    def _get_avatax_description(self):
        return 'Sales Order'

    def _get_invoice_grouping_keys(self):
        res = super()._get_invoice_grouping_keys()
        if self.filtered('fiscal_position_id.is_avatax'):
            res += ['partner_shipping_id']
        return res

    def _create_invoices(self, grouped=False, final=False, date=None):
        res = super()._create_invoices(grouped=grouped, final=final, date=date)
        res.button_update_avatax()
        return res


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    def _compute_tax_id(self):
        super(SaleOrderLine, self.filtered(lambda l: not l.order_id.fiscal_position_id.is_avatax))._compute_tax_id()
