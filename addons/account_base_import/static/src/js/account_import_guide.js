/** @odoo-module **/

import { ControlPanel } from "@web/search/control_panel/control_panel";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const { Component, onWillStart, onWillRender } = owl;

export class AccountImportGuide extends Component {
    setup() {
        this.actionService = useService("action");
        this.orm = useService("orm");
        this.env.config.setDisplayName(this.env._t("Accounting Import Guide"))
        onWillStart(async () => {
            const current_company_id = this.env.services.company.currentCompany.id
            this.data = await this.orm.searchRead("res.company", [["id", "=", current_company_id]], ["country_code"])
            this.isFecImportModuleInstalled = await this.orm.searchCount("ir.module.module", [["name", "=", "l10n_fr_fec_import"], ["state", "=", "installed"]]);
        });
        onWillRender(() => {
            this.countryCode = this.data[0].country_code
        });
    }

    _importAccountGuideAction(action) {
        this.actionService.doAction(action);
    }

    _openModuleInstallation(module) {
        this.actionService.doAction({
            name: this.env._t("Install a module"),
            res_model: "ir.module.module",
            type: "ir.actions.act_window",
            views: [[false, "kanban"], [false, "list"], [false, "form"]],
            view_mode: "kanban,list,form",
            context: {
                "search_default_name": module,
                "search_default_extra": true,
            },
        });
    }
};
AccountImportGuide.template = "account_base_import.accountImportTemplate";
AccountImportGuide.components = { ControlPanel };

registry.category("actions").add("account_import_guide", AccountImportGuide);
