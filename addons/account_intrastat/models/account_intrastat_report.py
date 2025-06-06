# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from itertools import zip_longest

from odoo import api, models, _
from odoo.tools import get_lang, OrderedSet
from odoo.tools.float_utils import float_repr, float_round

from odoo.tools.misc import format_date
from datetime import datetime
from collections import defaultdict

_merchandise_export_code = {
    'BE': '29',
    'FR': '21',
    'NL': '7',
}

_merchandise_import_code = {
    'BE': '19',
    'FR': '11',
    'NL': '6',
}

_unknown_country_code = {
    'BE': 'QU',
    'NL': 'QV',
}

_qn_unknown_individual_vat_country_codes = ('FI', 'SE', 'SK', 'DE', 'AT')

errors = (
    ('expired_trans', 'move_line_id'),
    ('premature_trans', 'move_line_id'),
    ('missing_trans', 'move_line_id'),
    ('expired_comm', 'product_id'),
    ('premature_comm', 'product_id'),
    ('missing_comm', 'product_id'),
    ('missing_unit', 'product_id'),
    ('missing_weight', 'product_id'),
)

REPORT_LINE_ID_KEYS = ['type', 'transaction_code', 'commodity_code', 'intrastat_product_origin_country_code', 'partner_vat', 'country_code', 'incoterm_code', 'transport_code', 'invoice_currency_id', 'region_code']

class IntrastatReportCustomHandler(models.AbstractModel):
    _name = 'account.intrastat.report.handler'
    _inherit = 'account.report.custom.handler'
    _description = 'Intrastat Report Custom Handler'

    def _dynamic_lines_generator(self, report, options, all_column_groups_expression_totals=None):
        if options.get('intrastat_grouped'):
            # dict of the form {move_id: {column_group_key: {expression_label: value}}}
            move_info_dict = {}

            # dict of the form {column_group_key: {expression_label: value}}
            total_values_dict = {}

            # Build query
            query_list = []
            full_query_params = []
            for column_group_key, column_group_options in report._split_options_per_column_group(options).items():
                query, params = self._build_query_group(column_group_options, column_group_key)
                query_list.append(f"({query})")
                full_query_params += params

            full_query = " UNION ALL ".join(query_list)
            self._cr.execute(full_query, full_query_params)
            results = self._cr.dictfetchall()
            # Fill dictionaries
            for res in results:
                line_id = self._get_report_line_id(res)
                column_group_key = res['column_group_key']

                current_move_info = move_info_dict.setdefault(line_id, {'name': self._get_move_info_name(res)})
                current_move_info[column_group_key] = res

                # We add the value to the total (for total line)
                total_values_dict.setdefault(column_group_key, {'value': 0})
                total_values_dict[column_group_key]['value'] += res['value']

                for error, dummy in errors:
                    if any(res.get(error, [])):
                        options.setdefault('intrastat_warnings', defaultdict(list))
                        options['intrastat_warnings'][error].extend(res[error])

            # Create lines
            lines = []
            for move_id, move_info in move_info_dict.items():
                line = self._create_report_line(options, move_info, move_id, ['value'])
                lines.append((0, line))

            # Create total line if only one type of invoice is selected
            if options.get('intrastat_total_line'):
                total_line = self._create_report_total_line(options, total_values_dict)
                lines.append((0, total_line))
        else:
            generated_lines = self._get_lines(options)
            load_more_limit = self._get_load_more_limit(options)
            lines = [(0, line) for line in (generated_lines[:load_more_limit] if load_more_limit else generated_lines)]
    
            report = self.env['account.report'].browse(options['report_id'])
            if load_more_limit and len(generated_lines) > load_more_limit:
                lines.append((0, report._get_load_more_line(len(lines), None, '_report_expand_unfoldable_line_intrastat_line', None, None, options)))
        return lines

    def _get_move_info_name(self, move_info):
        # Some values are necessary for the id generation but not in the name, we removed region_code because a condition handles it after
        keys = OrderedSet(REPORT_LINE_ID_KEYS) - OrderedSet(['incoterm_code', 'transport_code', 'invoice_currency_id', 'region_code'])
        name = " - ".join(str(move_info[key]) for key in keys)

        if self._show_region_code():
            name += f" - {move_info['region_code']}"
        return name

    def _get_report_line_id(self, move_info):
        move_values = []
        for key in REPORT_LINE_ID_KEYS:
            if key == 'intrastat_product_origin_country_code' and move_info.get(key) == 'XU':
                # Special case for the United Kingdom where the code is XU instead of GB,
                # to avoid issue when we fetch children lines, we set to GB in the line id.
                move_values.append('GB')
            else:
                move_values.append(str(move_info.get(key)))

        return self.env['account.report']._get_generic_line_id('account.move', None, markup=",".join(move_values))

    def _custom_options_initializer(self, report, options, previous_options=None):
        super()._custom_options_initializer(report, options, previous_options=previous_options)
        previous_options = previous_options or {}

        # Filter only partners with VAT
        options['intrastat_with_vat'] = previous_options.get('intrastat_with_vat', False)

        options['intrastat_grouped'] = False

        # Filter types of invoices
        default_type = [
            {'name': _('Arrival'), 'selected': False, 'id': 'arrival'},
            {'name': _('Dispatch'), 'selected': False, 'id': 'dispatch'},
        ]
        options['intrastat_type'] = previous_options.get('intrastat_type', default_type)
        options['country_format'] = previous_options.get('country_format')
        options['commodity_flow'] = previous_options.get('commodity_flow')

        # Filter the domain based on the types of invoice selected
        include_arrivals = options['intrastat_type'][0]['selected']
        include_dispatches = options['intrastat_type'][1]['selected']
        if not include_arrivals and not include_dispatches:
            include_arrivals = include_dispatches = True

        invoice_types = []
        if include_arrivals:
            invoice_types += ['in_invoice', 'out_refund']
        if include_dispatches:
            invoice_types += ['out_invoice', 'in_refund']

        # When only one type is selected, we can display a total line
        options['intrastat_total_line'] = include_arrivals != include_dispatches
        options.setdefault('forced_domain', []).append(('move_id.move_type', 'in', invoice_types))

        # Filter report type (extended form)
        options['intrastat_extended'] = previous_options.get('intrastat_extended', True)

        # 2 columns are conditional and should only appear when rendering the extended intrastat report
        # Some countries don't use the region code column; we hide it for them.
        excluded_columns = set()
        if not options['intrastat_extended']:
            excluded_columns |= {'transport_code', 'incoterm_code'}
        if not self._show_region_code():
            excluded_columns.add('region_code')

        new_columns = []
        for col in options['columns']:
            if col['expression_label'] not in excluded_columns:
                new_columns.append(col)

                # Replace country names by codes if necessary (for file exports)
                if options.get('country_format') == 'code':
                    if col['expression_label'] == 'country_name':
                        col['expression_label'] = 'country_code'
                    elif col['expression_label'] == 'intrastat_product_origin_country_name':
                        col['expression_label'] = 'intrastat_product_origin_country_code'
        options['columns'] = new_columns

        # Only pick Sale/Purchase journals (+ divider)
        report._init_options_journals(options, previous_options=previous_options, additional_journals_domain=[('type', 'in', ('sale', 'purchase'))])

        # When printing the report to xlsx, we want to use country codes instead of names
        xlsx_button_option = next(button_opt for button_opt in options['buttons'] if button_opt.get('action_param') == 'export_to_xlsx')
        xlsx_button_option['action_param'] = 'export_to_xlsx'
        options['ignore_totals_below_sections'] = True


    ####################################################
    # OVERRIDES
    ####################################################

    def _show_region_code(self):
        """Return a bool indicating if the region code is to be displayed for the country concerned in this localisation."""
        # TO OVERRIDE
        return True

    ####################################################
    # OPTIONS: INIT
    ####################################################

    def export_to_xlsx(self, options, response=None):
        # We need to regenerate the options to make sure we hide the country name columns as expected.
        report = self.env['account.report'].browse(options['report_id'])
        new_options = report._get_options(previous_options={**options, 'country_format': 'code', 'commodity_flow': 'code'})
        return report.export_to_xlsx(new_options, response=response)

    ####################################################
    # REPORT LINES: CORE
    ####################################################

    @api.model
    def _create_report_line(self, options, line_vals, line_id, number_values):
        """
        This function is called when generating the report. It creates an aggregate report line
        that has this format: Country Code - Currency - Commodity Code.
        It also contains the country columns and the commodity code has well as the value and the system
        if only one type of (Arrival or Dispatch) is selected.
        :param options: report options
        :param line_vals: values necessary for the line
        :param line_id: id of the line
        :param number_values: list of expression labels that need to have the 'number' class
        """
        report = self.env['account.report']
        columns = []
        uom_precision = self.env['decimal.precision'].precision_get('Product Unit of Measure')
        for column in options['columns']:
            expression_label = column['expression_label']
            value = line_vals.get(column['column_group_key'], {}).get(expression_label, False)

            if value:
                if options.get('commodity_flow') != 'code' and column['expression_label'] == 'system':
                    value = f"{value} ({line_vals.get(column['column_group_key'], {}).get('type', False)})"
                elif column['expression_label'] == 'supplementary_units':
                    value = float_repr(float_round(value, precision_digits=uom_precision), precision_digits=uom_precision)

            columns.append({
                'name': report.format_value(value, figure_type=column['figure_type']) if value else None,
                'no_format': value,
                'class': 'number' if expression_label in number_values else '',
            })

        unfold_all = self._context.get('print_mode') or options.get('unfold_all')
        return {
            'id': line_id,
            'name': line_vals['name'],
            'columns': columns,
            'unfoldable': True,
            'unfolded': unfold_all or line_id in options.get('unfolded_lines'),
            'expand_function': '_report_expand_unfoldable_line_intrastat_line',
            'level': 1,
            'class': 'account_intrastat_line_name',
        }

    def _report_expand_unfoldable_line_intrastat_line(self, line_dict_id, groupby, options, progress, offset, unfold_all_batch_data=None):
        options['account_report'] = self.env['account.report'].browse(options['report_id'])
        lines = self._get_lines(options, line_dict_id, offset)
        load_more_limit = self._get_load_more_limit(options)
        lines_to_render = lines[:load_more_limit] if load_more_limit else lines
        return {
            'lines': lines_to_render,
            'offset_increment': len(lines_to_render),
            'has_more': len(lines) > len(lines_to_render),
        }

    def _get_load_more_limit(self, options):
        report = self.env['account.report'].browse(options['report_id'])
        return report.load_more_limit or None if not self._context.get('print_mode') else None

    def _get_lines(self, options, parent_line=None, offset=0):
        """ This functions gets every line (account.move.line) that matches the selected options. """
        report = self.env['account.report'].browse(options['report_id'])
        expanded_line_options = None
        if parent_line:
            expanded_line_options = self._get_markup_info_from_intrastat_id(parent_line)

        is_intrastat_warnings_added = not options.get('intrastat_warnings')  # Check if the intrastat warning aren't already added
        queries = []
        full_query_params = []
        for column_group_key, column_group_options in report._split_options_per_column_group(options).items():
            query, params, = self._prepare_query(column_group_options, column_group_key, expanded_line_options, offset, report.load_more_limit + 1 if not self._context.get('print_mode') else None)
            queries.append(query)
            full_query_params += params

        full_query = " UNION ALL ".join(queries)
        self._cr.execute(full_query, full_query_params)
        raw_intrastat_lines = self._cr.dictfetchall()
        raw_intrastat_lines = self._fill_missing_values(raw_intrastat_lines)

        lines = []
        for raw_intrastat_line in raw_intrastat_lines:
            for error, val_key in errors:
                if raw_intrastat_line.get(error) and is_intrastat_warnings_added:
                    options.setdefault('intrastat_warnings', defaultdict(list))
                    options['intrastat_warnings'][error].append(raw_intrastat_line[val_key])

            lines.append(self._get_aml_line(report, parent_line, options, raw_intrastat_line))

        return lines

    def _get_aml_line(self, report, parent_line_id, options, aml_data):
        line_columns = []
        for column in options['columns']:
            col_expr_label = column['expression_label']
            col_value = aml_data.get(col_expr_label)

            if col_value is None or column.get('column_group_key') != aml_data.get('column_group_key'):
                line_columns.append({
                    'name': None,
                    'no_format': None,
                    'class': '',
                })
            else:
                col_class = ''
                formatted_value = col_value
                if options.get('commodity_flow') != 'code' and column['expression_label'] == 'system':
                    formatted_value = f"{col_value} ({aml_data.get('type', False)})"
                elif col_expr_label == 'date':
                    formatted_value = format_date(self.env, col_value)
                    col_class = 'date'
                elif col_expr_label == 'value':
                    formatted_value = report.format_value(col_value, figure_type=column['figure_type'], blank_if_zero=False)
                    col_class = 'number'
                line_columns.append({
                    'name': formatted_value,
                    'no_format': col_value,
                    'class': col_class,
                })

        return {
            'id': report._get_generic_line_id('account.move.line', aml_data['id']),
            'caret_options': 'account.move.line',
            'parent_id': parent_line_id,
            'name': aml_data['name'],
            'columns': line_columns,
            'level': 3,
        }

    @api.model
    def _create_report_total_line(self, options, total_vals):
        """ Create a total line for the report

        :param options: report options
        :param total_vals: total values dict
        """
        report = self.env['account.report']
        columns = []
        for column in options['columns']:
            expression_label = column['expression_label']
            value = total_vals.get(column['column_group_key'], {}).get(expression_label, False)

            columns.append({
                'name': report.format_value(value, figure_type=column['figure_type']) if value else None,
                'no_format': value,
                'class': 'number',
            })
        return {
            'id': report._get_generic_line_id(None, None, markup='total'),
            'name': _('Total'),
            'class': 'total',
            'level': 1,
            'columns': columns,
        }

    ####################################################
    # REPORT LINES: QUERY
    ####################################################

    @api.model
    def _prepare_query(self, options, column_group_key=None, expanded_line_options=None, offset=0, limit=None):
        query_blocks, where_params = self._build_query(options, column_group_key, expanded_line_options, offset, limit)
        query = f"({query_blocks['select']} {query_blocks['from']} {query_blocks['where']} {query_blocks['order']} {query_blocks['limit']})"
        return query, where_params

    @api.model
    def _build_query(self, options, column_group_key=None, expanded_line_options=None, offset=0, limit=None):
        def format_where_params(option_key, comparison_value=('None',)):
            if expanded_line_options[option_key] not in comparison_value:
                return expanded_line_options[option_key]
            return None

        domain = None
        if expanded_line_options:
            Move = self.env['account.move']
            move_types = Move.get_outbound_types(include_receipts=False) if expanded_line_options['type'] == 'Arrival' else Move.get_inbound_types(include_receipts=False)
            domain = [('move_id.move_type', 'in', move_types)]

        # triangular use cases are handled by letting the intrastat_country_id editable on
        # invoices. Modifying or emptying it allow to alter the intrastat declaration
        # accordingly to specs (https://www.nbb.be/doc/dq/f_pdf_ex/intra2017fr.pdf (§ 4.x))
        tables, where_clause, where_params = self.env['account.report'].browse(options['report_id'])._query_get(options, 'strict_range', domain=domain)

        import_merchandise_code = _merchandise_import_code.get(self.env.company.country_id.code, '29')
        export_merchandise_code = _merchandise_export_code.get(self.env.company.country_id.code, '19')
        unknown_individual_vat = 'QN999999999999' if self.env.company.country_id.code in _qn_unknown_individual_vat_country_codes else 'QV999999999999'
        unknown_country_code = _unknown_country_code.get(self.env.company.country_id.code, 'QV')
        weight_category_id = self.env['ir.model.data']._xmlid_to_res_id('uom.product_uom_categ_kgm')
        currency_table_query = self.env['res.currency']._get_query_currency_table(options)

        select = f"""
            SELECT
                %s AS column_group_key,
                row_number() over () AS sequence,
                CASE WHEN account_move.move_type IN ('in_invoice', 'out_refund') THEN %s ELSE %s END AS system,
                country.code AS country_code,
                COALESCE(country.name->>'{self.env.user.lang or get_lang(self.env).code}', country.name->>'en_US') AS country_name,
                company_country.code AS comp_country_code,
                transaction.code AS transaction_code,
                company_region.code AS region_code,
                comp_partner.vat as company_vat,
                code.code AS commodity_code,
                account_move_line.id AS id,
                prodt.id AS template_id,
                prodt.categ_id AS category_id,
                account_move_line.product_uom_id AS uom_id,
                inv_line_uom.category_id AS uom_category_id,
                account_move.id AS invoice_id,
                account_move_line.id as move_line_id,
                account_move.currency_id AS invoice_currency_id,
                account_move.name,
                COALESCE(account_move.date, account_move.invoice_date) AS invoice_date,
                account_move.move_type AS invoice_type,
                COALESCE(inv_incoterm.code, comp_incoterm.code) AS incoterm_code,
                COALESCE(inv_transport.code, comp_transport.code) AS transport_code,
                CASE WHEN account_move.move_type IN ('in_invoice', 'out_refund') THEN 'Arrival' ELSE 'Dispatch' END AS type,
                ROUND(
                    COALESCE(prod.weight, 0) * account_move_line.quantity / (
                        CASE WHEN inv_line_uom.category_id IS NULL OR inv_line_uom.category_id = prod_uom.category_id
                        THEN inv_line_uom.factor ELSE 1 END
                    ) * (
                        CASE WHEN prod_uom.uom_type <> 'reference'
                        THEN prod_uom.factor ELSE 1 END
                    ),
                    SCALE(ref_weight_uom.rounding)
                ) AS weight,
                account_move_line.quantity / (
                    CASE WHEN inv_line_uom.category_id IS NULL OR inv_line_uom.category_id = prod_uom.category_id
                    THEN inv_line_uom.factor ELSE 1 END
                ) AS quantity,
                CASE WHEN code.supplementary_unit IS NOT NULL and prod.intrastat_supplementary_unit_amount != 0
                    THEN ROUND(CAST(
                        prod.intrastat_supplementary_unit_amount * (
                        account_move_line.quantity / (
                            CASE WHEN inv_line_uom.category_id IS NULL OR inv_line_uom.category_id = prod_uom.category_id
                            THEN inv_line_uom.factor ELSE 1 END
                        )) AS numeric), 2)
                    ELSE NULL END AS supplementary_units,
                account_move_line.quantity AS line_quantity,
                -- We double sign the balance to make sure that we keep consistency between invoice/bill and the intrastat report
                -- Example: An invoice selling 10 items (but one is free 10 - 1), in the intrastat report we'll have 2 lines
                -- One for 10 items minus one for the free item
                SIGN(account_move_line.quantity) * SIGN(account_move_line.price_unit) * ABS(ROUND(account_move_line.balance * currency_table.rate, currency_table.precision)) AS value,
                CASE WHEN product_country.code = 'GB' THEN 'XU' ELSE COALESCE(product_country.code, %s) END AS intrastat_product_origin_country_code,
                COALESCE(product_country.name->>'{self.env.user.lang or get_lang(self.env).code}', product_country.name->>'en_US') AS intrastat_product_origin_country_name,
                CASE WHEN partner.vat IS NOT NULL THEN partner.vat
                     WHEN partner.vat IS NULL AND partner.is_company IS FALSE THEN %s
                     ELSE 'QV999999999999'
                END AS partner_vat,
                transaction.expiry_date <= account_move.invoice_date AS expired_trans,
                transaction.start_date > account_move.invoice_date AS premature_trans,
                transaction.id IS NULL AS missing_trans,
                code.expiry_date <= account_move.invoice_date AS expired_comm,
                code.start_date > account_move.invoice_date AS premature_comm,
                code.id IS NULL as missing_comm,
                COALESCE(prod.intrastat_supplementary_unit_amount, 0) = 0 AND code.supplementary_unit IS NOT NULL as missing_unit,
                COALESCE(prod.weight, 0) = 0 AND code.supplementary_unit IS NULL AS missing_weight,
                prod.id AS product_id,
                prodt.categ_id AS template_categ,
                prodt.description as goods_description
        """
        from_ = f"""
            FROM
                {tables}
                JOIN {currency_table_query} ON currency_table.company_id = account_move_line.company_id
                JOIN account_move ON account_move.id = account_move_line.move_id
                LEFT JOIN account_intrastat_code transaction ON account_move_line.intrastat_transaction_id = transaction.id
                LEFT JOIN res_company company ON account_move.company_id = company.id
                LEFT JOIN account_intrastat_code company_region ON company.intrastat_region_id = company_region.id
                LEFT JOIN res_partner partner ON account_move_line.partner_id = partner.id
                LEFT JOIN res_partner comp_partner ON company.partner_id = comp_partner.id
                LEFT JOIN res_country country ON account_move.intrastat_country_id = country.id
                LEFT JOIN res_country company_country ON comp_partner.country_id = company_country.id
                INNER JOIN product_product prod ON account_move_line.product_id = prod.id
                LEFT JOIN product_template prodt ON prod.product_tmpl_id = prodt.id
                LEFT JOIN account_intrastat_code code ON code.id = prod.intrastat_code_id
                LEFT JOIN uom_uom inv_line_uom ON account_move_line.product_uom_id = inv_line_uom.id
                LEFT JOIN uom_uom prod_uom ON prodt.uom_id = prod_uom.id
                LEFT JOIN account_incoterms inv_incoterm ON account_move.invoice_incoterm_id = inv_incoterm.id
                LEFT JOIN account_incoterms comp_incoterm ON company.incoterm_id = comp_incoterm.id
                LEFT JOIN account_intrastat_code inv_transport ON account_move.intrastat_transport_mode_id = inv_transport.id
                LEFT JOIN account_intrastat_code comp_transport ON company.intrastat_transport_mode_id = comp_transport.id
                LEFT JOIN res_country product_country ON product_country.id = account_move_line.intrastat_product_origin_country_id
                LEFT JOIN res_country partner_country ON partner.country_id = partner_country.id AND partner_country.intrastat IS TRUE
                LEFT JOIN uom_uom ref_weight_uom on ref_weight_uom.category_id = %s and ref_weight_uom.uom_type = 'reference'
        """
        where = f"""
            WHERE
                {where_clause}
                AND account_move_line.display_type = 'product'
                AND (account_move_line.price_subtotal != 0 OR account_move_line.price_unit * account_move_line.quantity != 0)
                AND company_country.id != country.id
                AND country.intrastat = TRUE AND (country.code != 'GB' OR account_move.date < '2021-01-01')
                AND prodt.type != 'service'
                AND ref_weight_uom.active
        """

        if expanded_line_options:
            # When expanding the grouped lines, we only want to add the ones that matches the parent country code, currency and commodity code
            where_values = {
                'country.code': format_where_params('country_code'),
                'code.code': format_where_params('commodity_code'),
                'product_country.code': format_where_params('intrastat_product_origin_country_code', comparison_value=('QV', 'QU')),
                'transaction.code': format_where_params('transaction_code'),
                'company_region.code': format_where_params('region_code'),
                'partner.vat': format_where_params('partner_vat', comparison_value=('QV999999999999', 'QN999999999999')),
                'COALESCE(inv_incoterm.code, comp_incoterm.code)': format_where_params('incoterm_code'),
                'COALESCE(inv_transport.code, comp_transport.code)': format_where_params('transport_code'),
                'account_move.currency_id': format_where_params('invoice_currency_id'),
            }
            for key, value in where_values.items():
                where += f" AND {key} IS NOT DISTINCT FROM %s"
                where_params.append(value)

        order = " ORDER BY account_move.invoice_date DESC, account_move_line.id"

        if options['intrastat_with_vat']:
            where += " AND partner.vat IS NOT NULL "

        query = {
            'select': select,
            'from': from_,
            'where': where,
            'order': order,
            'limit': "",
        }

        query_params = [
            column_group_key,
            import_merchandise_code,
            export_merchandise_code,
            unknown_country_code,
            unknown_individual_vat,
            weight_category_id,
            *where_params,
        ]

        if limit:
            query['limit'] = """
                OFFSET %s
                LIMIT %s
            """
            query_params.extend([
                offset,
                limit,
            ])

        return query, query_params

    @api.model
    def _build_query_group(self, options, column_group_key=None):
        """ This is the query to have the line grouped by country, currency and commodity code. """
        inner_query, params = self._prepare_query(options, column_group_key, offset=0, limit=None)

        query = f"""
          SELECT %s AS column_group_key,
                 intrastat_lines.system as system,
                 intrastat_lines.type as type,
                 intrastat_lines.country_code AS country_code,
                 intrastat_lines.transaction_code as transaction_code,
                 intrastat_lines.transport_code as transport_code,
                 intrastat_lines.region_code as region_code,
                 intrastat_lines.commodity_code AS commodity_code,
                 intrastat_lines.country_name as country_name,
                 intrastat_lines.partner_vat as partner_vat,
                 intrastat_lines.incoterm_code as incoterm_code,
                 intrastat_lines.intrastat_product_origin_country_name as intrastat_product_origin_country_name,
                 intrastat_lines.intrastat_product_origin_country_code as intrastat_product_origin_country_code,
                 intrastat_lines.invoice_currency_id as invoice_currency_id,
                 ARRAY_AGG(CASE WHEN intrastat_lines.expired_trans IS TRUE THEN intrastat_lines.move_line_id ELSE NULL END) as expired_trans,
                 ARRAY_AGG(CASE WHEN intrastat_lines.premature_trans IS TRUE THEN intrastat_lines.move_line_id ELSE NULL END) as premature_trans,
                 ARRAY_AGG(CASE WHEN intrastat_lines.missing_trans IS TRUE THEN intrastat_lines.move_line_id ELSE NULL END) as missing_trans,
                 ARRAY_AGG(CASE WHEN intrastat_lines.expired_comm IS TRUE THEN intrastat_lines.product_id ELSE NULL END) as expired_comm,
                 ARRAY_AGG(CASE WHEN intrastat_lines.premature_comm IS TRUE THEN intrastat_lines.product_id ELSE NULL END) as premature_comm,
                 ARRAY_AGG(CASE WHEN intrastat_lines.missing_comm IS TRUE THEN intrastat_lines.product_id ELSE NULL END) as missing_comm,
                 ARRAY_AGG(CASE WHEN intrastat_lines.missing_unit IS TRUE THEN intrastat_lines.product_id ELSE NULL END) as missing_unit,
                 ARRAY_AGG(CASE WHEN intrastat_lines.missing_weight IS TRUE THEN intrastat_lines.product_id ELSE NULL END) as missing_weight,
                 SUM(intrastat_lines.value) as value,
                 SUM(intrastat_lines.weight) as weight,
                 SUM(intrastat_lines.supplementary_units) as supplementary_units
            FROM ({inner_query}) intrastat_lines
      INNER JOIN account_move ON account_move.id = intrastat_lines.invoice_id
        GROUP BY system, type, country_code, transaction_code, transport_code, region_code, commodity_code, country_name, partner_vat,
                 incoterm_code,intrastat_product_origin_country_code, intrastat_product_origin_country_name, invoice_currency_id
            """

        params = [
            column_group_key,
            *params,
        ]

        return query, params


    ####################################################
    # REPORT LINES: HELPERS
    ####################################################

    @api.model
    def _fill_missing_values(self, vals_list):
        """ Template method to be overidden to retrieve complex data

            :param vals_list:    A dictionary created by the dictfetchall method.
        """
        return vals_list

    def _get_markup_info_from_intrastat_id(self, line_id):
        """ This function gets necessary info present in the generic report line id.
            This information are related to REPORT_LINE_ID_KEYS.

            :param line_id: A generic report line id
            :return: A dictionary containing as key all the values in REPORT_LINE_ID_KEYS
                     and as value what we found in the generic report line id. If we don't
                     have a related value, we fill it with 'None'.
        """
        markup = self.env['account.report']._get_markup(line_id)
        line = markup.split(',')
        return {
            key: value
            for key, value in zip_longest(REPORT_LINE_ID_KEYS, line, fillvalue='None')
        }

    ####################################################
    # ACTIONS
    ####################################################

    def action_invalid_code_moves(self, options, params):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Invalid transaction intrastat code entries.'),
            'res_model': 'account.move.line',
            'views': [(
                self.env.ref('account_intrastat.account_move_line_tree_view_account_intrastat_transaction_codes').id,
                'list',
            ), (False, 'form')],
            'domain': [('id', 'in', options['intrastat_warnings'][params['option_key']])],
            'context': {
                'create': False,
                'delete': False,
                'expand': True,
            },
        }

    def action_invalid_code_products(self, options, params):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Invalid commodity intrastat code products.'),
            'res_model': 'product.product',
            'views': [(
                self.env.ref('account_intrastat.product_product_tree_view_account_intrastat').id,
                'list',
            ), (False, 'form')],
            'domain': [('id', 'in', options['intrastat_warnings'][params['option_key']])],
            'context': {
                'create': False,
                'delete': False,
                'expand': True,
            },
        }

    def action_undefined_units_products(self, options, params):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Undefined supplementary unit products.'),
            'res_model': 'product.product',
            'views': [(
                self.env.ref('account_intrastat.product_product_tree_view_account_intrastat_supplementary_unit').id,
                'list',
            ), (False, 'form')],
            'domain': [('id', 'in', options['intrastat_warnings'][params['option_key']])],
            'context': {
                'create': False,
                'delete': False,
                'expand': True,
            },
        }

    def action_undefined_weight_products(self, options, params):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Undefined weight products.'),
            'res_model': 'product.product',
            'views': [(
                self.env.ref('account_intrastat.product_product_tree_view_account_intrastat_weight').id,
                'list',
            ), (False, 'form')],
            'domain': [('id', 'in', options['intrastat_warnings'][params['option_key']])],
            'context': {
                'create': False,
                'delete': False,
                'expand': True,
            },
        }
