/** @odoo-module */
import { browser } from "@web/core/browser/browser";
const localStorage = browser.localStorage;

import config from "web.config";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { FormRenderer } from '@web/views/form/form_renderer';
import { KnowledgeCoverDialog } from '@knowledge/components/cover_selector/knowledge_cover_dialog';
import KnowledgeTreePanelMixin from '@knowledge/js/tools/tree_panel_mixin';
import { patch } from "@web/core/utils/patch";
import MoveArticleDialog from "@knowledge/components/move_article_dialog/move_article_dialog";
import PermissionPanel from '@knowledge/components/permission_panel/permission_panel';
import { sprintf } from '@web/core/utils/strings';
import { useService } from "@web/core/utils/hooks";

const { onMounted, onPatched, onWillDestroy, useEffect, useRef, useState } = owl;

export class KnowledgeArticleFormRenderer extends FormRenderer {

    //--------------------------------------------------------------------------
    // Component
    //--------------------------------------------------------------------------
    setup() {
        super.setup();

        this.state = useState({
            displayChatter: false,
            displaySharePanel: false,
            displayPropertyPanel: false,
            displayPropertyToggle: false,
        });

        this.actionService = useService("action");
        this.dialog = useService("dialog");
        this.messagingService = useService("messaging");
        this.orm = useService("orm");
        this.rpc = useService("rpc");

        this.root = useRef('root');
        this.tree = useRef('tree');

        this.loadEmoji = this.messagingService.get().then(messaging => {
            this.messaging = messaging;
            this.messaging.messagingBus.addEventListener('knowledge_add_emoji', this._onAddEmoji);
            this.messaging.messagingBus.addEventListener('knowledge_remove_emoji', this._onRemoveEmoji);
            if (this.messaging.emojiRegistry.isLoaded || this.messaging.emojiRegistry.isLoading) {
                return messaging;
            } else {
                this.messaging.emojiRegistry.loadEmojiData();
                return messaging;
            }
        });
        this._onAddEmoji = this._onAddEmoji.bind(this);
        this._onRemoveEmoji = this._onRemoveEmoji.bind(this);

        this.device = config.device;
        this.sidebarSize = localStorage.getItem('knowledgeArticleSidebarSize');

        onPatched(() => {
            // Handle Add property button
            if (this.triggerClickOnAddProperty && this.root.el.querySelector('.o_field_property_add > button')) {
                this.triggerClickOnAddProperty = false;
                this.root.el.querySelector('.o_field_property_add > button').click();
            }
        });

        useEffect(
            () => {
                if (this.props.record.data.user_can_write && !this.props.record.data.is_locked) {
                    this._resizeNameInput(this.props.record.data.name);
                }
            }, () => [this.props.record.data.id]
        );

        useEffect(() => {
            // ADSC: Make tree component with "t-on-" instead of adding these eventListeners
            const listener = (ev) => {
                const target = ev.target;
                if (target.classList.contains('o_article_name')) {
                    this.openArticle(parseInt(target.closest('.o_article').dataset.articleId));
                } else if (target.classList.contains('o_article_emoji')) {
                    this._showEmojiPicker(ev);
                } else if (target.classList.contains('o_knowledge_article_load_more')) {
                    this._loadMoreArticles(ev);
                } else {
                    const button = target.closest('button');
                    if (!button) {
                        return;
                    }
                    const section = button.closest('section');
                    const isFavoriteSection = section.classList.contains('o_favorite_container');
                    if (button.classList.contains('o_section_create')) {
                        this.createArticle(section.dataset.section);
                    } else if (button.classList.contains('o_article_create')) {
                        const parentId = parseInt(button.closest('.o_article').dataset.articleId);
                        this.createArticle(undefined, parentId);
                        this._addUnfolded(parentId.toString(), false);
                        // If create from favorite tree, force unfold the parent
                        if (isFavoriteSection) {
                            this._addUnfolded(parentId.toString(), true);
                        }
                    } else if (button.classList.contains('o_article_caret')) {
                        this._fold($(button), isFavoriteSection);
                    }
                }
            };
            this.tree.el.addEventListener('click', listener);

            return () => {
                this.tree.el.removeEventListener('click', listener);
            };
        }, () => []);

        /**
         * Show/hide the Share Panel (invite, update members permissions, ...)
         * Done on events of BS dropdown instead of onClick because user might
         * click on another dropdown toggle button, which would not fire the
         * click on toggle Share Panel itself. This leads to an inconsistent
         * state between the display and the state, which is solved by correctly
         * using dropdown events.
        */
        useEffect((buttonSharePanel) => {
            if (buttonSharePanel) {
                buttonSharePanel.addEventListener(
                    // Prevent hiding the dropdown when the invite modal is shown
                    'hide.bs.dropdown', (ev) => {
                        if (this.uiService.activeElement !== document) {
                            ev.preventDefault();
                        }
                });
                buttonSharePanel.addEventListener(
                    'shown.bs.dropdown',
                    () => this.state.displaySharePanel = true
                );
                buttonSharePanel.addEventListener(
                    'hidden.bs.dropdown',
                    () => this.state.displaySharePanel = false
                );
            }
        }, () => [document.querySelector('#dropdown_share_panel')]);

        onMounted(() => {
            this._renderTree(this.resId, '/knowledge/tree_panel');

            // Focus inside the body if article is empty (default_focus does not work).
            const body = this.root.el.querySelector('.o_knowledge_editor .note-editable');
            if (body && !body.innerText.trim()) {
                setTimeout(() => body.focus(), 0);
            }
        });

        useEffect(() => {
            // If the article has some properties set,
            // we should display the property panel that is hidden by default.
            const displayPropertyPanel = this.props.record.data.article_properties && this.props.record.data.article_properties.length > 0;
            this.state.displayPropertyToggle = displayPropertyPanel;
            this.state.displayPropertyPanel = displayPropertyPanel;
        }, () => {
            return [this.props.record.data.article_properties && this.props.record.data.article_properties.length > 0];
        });

        onWillDestroy(async () => {
            const messaging = await this.loadEmoji;
            messaging.messagingBus.removeEventListener('knowledge_add_emoji', this._onAddEmoji);
            messaging.messagingBus.removeEventListener('knowledge_remove_emoji', this._onRemoveEmoji);
        });
    }


    //--------------------------------------------------------------------------
    // Public
    //--------------------------------------------------------------------------

    /**
     * Adds a random cover using unsplash. If unsplash throws an error (service
     * down/keys unset), opens the cover selector instead.
     * @param {Event} event
     */
    async addCover(event) {
        // Disable button to prevent multiple calls
        event.target.classList.add('disabled');
        if (this.props.record.data.name === this.env._t('Untitled')) {
            // Rename the article if there is a title in the body
            await this._rename(this._getFallbackTitle());
        }
        const articleName = this.props.record.data.name;
        try {
            const res = await this.rpc(`/knowledge/article/${this.resId}/add_random_cover`, {
                query: articleName === this.env._t('Untitled') ? '' : articleName,
                orientation: 'landscape',
            });
            if (res.cover_id) {
                await this.props.record.update({cover_image_id: [res.cover_id]});
            } else {
                this.openCoverSelector();
            }
        } catch {
            this.openCoverSelector();
        }
        event.target.classList.remove('disabled');
    }

    /**
     * Add a random icon to the article.
     */
    addIcon() {
        if (!this.messaging || !this.messaging.knowledge) {
            return;
        }
        const icon = this.messaging.knowledge.randomEmojis[Math.floor(Math.random() * this.messaging.knowledge.randomEmojis.length)].codepoints;
        this._renderEmoji(icon, this.resId);
    }

    /**
     * Open the Properties Panel and start to add the first property field.
     */
    addProperties(event) {
        // See onPatched: triggers a click when properties widget is ready and added
        // in to DOM. It adds a property directly.
        this.triggerClickOnAddProperty = true;
        this.state.displayPropertyToggle = true;
        this.state.displayPropertyPanel = true;
    }

    /**
     * Copy the current article in private section and open it.
     */
    async copyArticleAsPrivate() {
        await this._saveIfDirty();
        const articleId = await this.orm.call(
            "knowledge.article",
            "action_make_private_copy",
            [this.resId]
        );
        this.openArticle(articleId, true);
    }

    /**
     * Create a new article and open it.
     * @param {String} category - Category of the new article
     * @param {integer} targetParentId - Id of the parent of the new article (optional)
     */
    async createArticle(category, targetParentId) {
        const articleId = await this.orm.call(
            "knowledge.article",
            "article_create",
            [],
            {
                is_private: category === 'private',
                parent_id: targetParentId ? targetParentId : false
            }
        );
        this.openArticle(articleId, true);
    }

    /**
     * @param {integer} - resId: id of the article to open
     * @param {boolean} - [forceFullReload]: will reload the whole form view using a "doAction"
     *   if true, notably useful when the tree panel needs to be reloaded (defaults to false).
     */
    async openArticle(resId, forceFullReload=false) {
        // If the focus is on the "name" input, force a _rename before leaving this article
        if (document.activeElement.id === "name") {
            await this._rename(document.activeElement.value);
            document.activeElement.blur();
        } else if (this.resId) {  // Don't save when NoRecord helper is shown
            await this.props.record.save();
        }

        if (resId) {
            if (forceFullReload) {
                this.actionService.doAction(
                    await this.orm.call('knowledge.article', 'action_home_page', resId ? [resId] : []),
                    {stackPosition: 'replaceCurrentAction'}
                );
            } else {
                // Ensures that all *selected* articles are unselected
                document.querySelectorAll('.o_article_active').forEach((active) => {
                    active.classList.remove('o_article_active', 'fw-bold', 'text-900');
                });
                document.querySelectorAll('.o_article_emoji_active').forEach((emoji) => {
                    emoji.classList.remove('o_article_emoji_active', 'text-900');
                });

                document.querySelectorAll(`[data-article-id="${resId}"] > div`).forEach((currentArticle) => {
                    currentArticle.classList.add('o_article_active', 'fw-bold', 'text-900');
                    const emoji = currentArticle.querySelector('.o_article_emoji');
                    if (emoji) {
                        emoji.classList.add('o_article_emoji_active', 'text-900');
                    }
                });

                // Force save if changes have been made before loading the new record
                await this._saveIfDirty();

                const scrollView = document.querySelector('.o_scroll_view_lg');
                if (scrollView) {
                    // hide the flicker
                    scrollView.style.visibility = 'hidden';
                    // Scroll up if we have a desktop screen
                    scrollView.scrollTop = 0;
                }

                const mobileScrollView = document.querySelector('.o_knowledge_main_view');
                if (mobileScrollView) {
                    // Scroll up if we have a mobile screen
                    mobileScrollView.scrollTop = 0;
                }

                // load the new record
                try {
                    await this.props.record.model.load({
                        resId: resId,
                    });
                } catch (_) {
                    this.actionService.doAction(
                        await this.orm.call('knowledge.article', 'action_home_page', [false]),
                        {stackPosition: 'replaceCurrentAction'}
                    );
                }

                if (scrollView) {
                    // Show loaded document
                    scrollView.style.visibility = 'visible';
                }

            }
        }
    }

    openCoverSelector() {
        const articleName = this.props.record.data.name;
        this.dialog.add(KnowledgeCoverDialog, {
            articleCoverId: this.props.record.data.cover_image_id[0],
            articleName: articleName === this.env._t('Untitled') ? '' : articleName,
            save: async (id) => this.props.record.update({cover_image_id: [id]})
        });
    }

    get resId() {
        return this.props.record.resId;
    }

    /**
     * Show/hide the chatter. When showing it, it fetches data required for
     * new messages, activities, ...
     */
    toggleChatter() {
        if (this.resId) {
            this.root.el.querySelector('.btn-chatter').classList.toggle('active');
            this.state.displayChatter = !this.state.displayChatter;
        }
    }

    /**
     * Show/hide the Property Fields right panel.
     */
    toggleProperties() {
        // This first time toggle properties is called is to display the property panel.
        // Once done, we should always display property toggle.
        this.state.displayPropertyToggle = true;
        this.state.displayPropertyPanel = !this.state.displayPropertyPanel;
    }

    /**
     * Add/Remove article from favorites and reload the favorite tree.
     * One does not use "record.update" since the article could be in readonly.
     * @param {event} Event
     */
    async toggleFavorite(event) {
        // Save in case name has been edited, so that this new name is used
        // when adding the article in the favorite section.
        await this._saveIfDirty();
        await this.orm.call(this.props.record.resModel, "action_toggle_favorite", [[this.resId]]);
        // Load to have the correct value for 'is_user_favorite'.
        await this.props.record.load();
        // Rerender the favorite button.
        await this.props.record.model.notify();
        // ADSC: move when tree component
        let unfoldedFavoriteArticlesIds = localStorage.getItem('knowledge.unfolded.favorite.ids');
        unfoldedFavoriteArticlesIds = unfoldedFavoriteArticlesIds ? unfoldedFavoriteArticlesIds.split(";").map(Number) : [];
        const template = await this.rpc(
            '/knowledge/tree_panel/favorites',
            {
                active_article_id: this.resId,
                unfolded_favorite_articles_ids: unfoldedFavoriteArticlesIds,
            }
        );
        this.tree.el.querySelector('.o_favorite_container').innerHTML = template;
        if (!this.device.isMobile) {
            this._setTreeFavoriteListener();
        }
    }


    //--------------------------------------------------------------------------
    // Handlers
    //--------------------------------------------------------------------------

    async onChangeCoverClick() {
        if (this.props.record.data.name === this.env._t('Untitled')) {
            // Rename the article if there is a title in the body
            await this._rename(this._getFallbackTitle());
        }
        this.openCoverSelector();
    }
    /**
     * Show the Dialog allowing to move the current article.
     */
    onMoveArticleClick() {
        this.dialog.add(
            MoveArticleDialog,
            {
                articleName: this.props.record.data.name,
                articleId: this.resId,
                category: this.props.record.data.category,
                moveArticle: this._moveArticle.bind(this),
                reloadTree: this._renderTree.bind(this),
            }
        );
    }

    /**
     * When the user clicks on the name of the article, checks if the article
     * name hasn't been set yet. If it hasn't, it will look for a title in the
     * body of the article and set it as the name of the article.
     * @param {Event} event
     */
    _onNameClick(event) {
        const name = event.target.value;
        if (name === 'Untitled' || name === this.env._t('Untitled')) {
            this._rename('');
        }
    }


    //--------------------------------------------------------------------------
    // Private
    //--------------------------------------------------------------------------    

    /**
     * Try to move the given article to the new given position.
     * @param {integer} articleId
     * @param {Object} position
     * @param {Function} onSuccess
     * @param {Function} onReject
     */
    async _confirmMoveArticle(articleId, position, onSuccess, onReject) {
        // Force save if changes have been made before the move to.
        await this._saveIfDirty();
        try {
            const result = await this.orm.call(
                'knowledge.article',
                'move_to',
                [articleId],
                position
            );
            if (result) {
                onSuccess();
                await this.props.record.load();
                this.env.model.notify();
            } else {
                onReject();
            }
        } catch (error) {
            onReject();
            throw error;
        }
    }
    
    /**
     * Fetch the template of the children of the given article to add in the
     * article tree.
     * @param {integer} parentId
     * 
     * @returns {Promise}
     */
    _fetchChildrenArticles(parentId) {
        return this.rpc(
            '/knowledge/tree_panel/children',
            {
                parent_id: parentId
            }
        );
    }

    /**
     * @return {string} - first h1 in the body, "Untitled" if not found
     */
    _getFallbackTitle() {
        const articleTitle = this.root.el.querySelector('.o_knowledge_editor .note-editable h1');
        if (articleTitle) {
            return articleTitle.textContent.trim() || this.env._t('Untitled');
        }
        return this.env._t('Untitled');
    }

    /**
     * @param {Object} data
     * @param {integer} data.article_id
     * @param {String} data.oldCategory
     * @param {String} data.newCategory
     * @param {integer} [data.target_parent_id]
     * @param {integer} [data.before_article_id]
     * @param {Function} data.onSuccess
     * @param {Function} data.onReject
     */
    async _moveArticle(data) {
        var newPosition = {
            category: data.newCategory,
        };
        if (typeof data.target_parent_id !== 'undefined') {
            newPosition.parent_id = data.target_parent_id;
        }
        if (typeof data.before_article_id !== 'undefined') {
            newPosition.before_article_id = data.before_article_id;
        }
        if (data.newCategory === data.oldCategory) {
            await this._confirmMoveArticle(data.article_id, newPosition, data.onSuccess, data.onReject);
        } else {
            const articleName = this.props.record.data.display_name;
            let message;
            if (data.newCategory === 'workspace') {
                message = sprintf(
                    this.env._t('Are you sure you want to move "%s" to the Workspace? It will be shared with all internal users.'),
                    articleName
                );
            } else if (data.newCategory === 'private') {
                message = sprintf(
                    this.env._t('Are you sure you want to move "%s" to private? Only you will be able to access it.'),
                    articleName
                );
            } else if (data.newCategory === 'shared' && data.target_parent_id) {
                const parent = document.querySelector(`[data-article-id='${data.target_parent_id}']`);
                const parentEmoji = parent.querySelector('.o_article_emoji').textContent || '';
                const parentName = parent.querySelector('.o_article_name').textContent || '';
                message = sprintf(
                    this.env._t('Are you sure you want to move "%s" under "%s%s"? It will be shared with the same persons.'),
                    articleName, parentEmoji, parentName
                );
            }
            this.dialog.add(ConfirmationDialog, {
                body: message,
                confirm: async () => await this._confirmMoveArticle(data.article_id, newPosition, data.onSuccess, data.onReject),
                cancel: data.onReject
            });
        }
    }

    /**
     * @private
     * @param {CustomEvent} ev
     * @param {Object} ev.detail
     * @param {KnowledgeArticle} param0.detail.article
     * @param {Emoji} param0.detail.emoji
     */
    _onAddEmoji(ev) {
        this._renderEmoji(ev.detail.emoji.codepoints, ev.detail.article.id);
    }

    /**
     * @private
     * @param {CustomEvent} ev
     * @param {Object} ev.detail
     * @param {KnowledgeArticle} param0.detail.article
     */
    _onRemoveEmoji(ev) {
        this._renderEmoji(false, ev.detail.article.id);
    }

    /**
     * Rename the article. To prevent empty names, checks for a valid title in
     * the article, or renames the article to the default article name.
     * @param {string} name - Target Name
     */
    async _rename(name) {
        if (name === '') {
            name = this._getFallbackTitle();
            // await here will cause the main flow tour to fail
            this.props.record.update({'name': name});
        } else if (name !== this.props.record.data.name) {
            await this.props.record.update({'name': name});
        }
        this._resizeNameInput(name);
        // ADSC: Remove when tree component
        // Updates the name in the sidebar
        const selector = `.o_article[data-article-id="${this.resId}"] > .o_article_handle > div > .o_article_name`;
        this.tree.el.querySelectorAll(selector).forEach(function(articleName) {
          articleName.textContent = name;
        });
    }

    /**
     * Render the updated emoji of the given article (if the article is the
     * current article, update the record first).
     * @param {String} unicode
     * @param {integer} articleId
     */
    _renderEmoji(unicode, articleId) {
        if (articleId === this.resId) {
            this.props.record.update({'icon': unicode});
        }
        // ADSC: remove when tree component
        // Updates the emojis in the sidebar
        const emojis = this.tree.el.querySelectorAll(`.o_article_emoji_dropdown[data-article-id="${articleId}"] > .o_article_emoji`);
        for (let idx = 0; idx < emojis.length; idx++) {
            emojis[idx].textContent = unicode || '📄';
        }
    }

    /**
     * Render the tree listing all articles.
     * To minimize loading time, the function will initially load the root articles.
     * The other articles will be loaded lazily: The user will have to click on
     * the carret next to an article to load and see their children.
     * The id of the unfolded articles will be cached so that they will
     * automatically be displayed on page load.
     * @param {integer} activeArticleId
     * @param {String} route
     */
    async _renderTree(activeArticleId, route) {
        let unfoldedArticlesIds = localStorage.getItem('knowledge.unfolded.ids');
        unfoldedArticlesIds = unfoldedArticlesIds ? unfoldedArticlesIds.split(";").map(Number) : false;
        let unfoldedFavoriteArticlesIds = localStorage.getItem('knowledge.unfolded.favorite.ids');
        unfoldedFavoriteArticlesIds = unfoldedFavoriteArticlesIds ? unfoldedFavoriteArticlesIds.split(";").map(Number) : false;
        // Force save article if it's dirty to keep up to date the article data before rendering the tree
        await this._saveIfDirty();
        try {
            const htmlTree = await this.rpc(route,
                {
                    active_article_id: activeArticleId,
                    unfolded_articles_ids: unfoldedArticlesIds,
                    unfolded_favorite_articles_ids: unfoldedFavoriteArticlesIds,
                }
            );
            this.tree.el.innerHTML = htmlTree;

            if (this.resId) {
                const scrollToArticle = document.querySelector(
                    `section[data-section="workspace"] [data-article-id="${this.resId}"] > div,
                     section[data-section="private"] [data-article-id="${this.resId}"] > div`
                );

                if (scrollToArticle) {
                    scrollToArticle.scrollIntoView();
                }
            }

            if (!this.device.isMobile) {
                this._setTreeListener();
                this._setTreeFavoriteListener();
            }
        } catch {
            this.tree.el.innerHTML = "";
        }
    }

    /**
     * Update the sequence of favortie articles for the curent user.
     * @param {Array} favoriteIds - Updated sequence
     */
    _resequenceFavorites(favoriteIds) {
        this.rpc(
            '/web/dataset/resequence',
            {
                model: "knowledge.article.favorite",
                ids: favoriteIds,
                offset: 1,
            }
        );
    }

    /**
     * Resize the name input by updating the value of the span hidden behind
     * the input.
     */
    _resizeNameInput(name) {
        this.root.el.querySelector('.o_breadcrumb_article_name_container > span').innerText = name;
    }

    async _saveIfDirty() {
        if (this.props.record.isDirty) {
            await this.props.record.save();
        }
    }

    /**
     * Initializes the drag-and-drop behavior of the tree listing all articles.
     * Once this function is called, the user will be able to move an article
     * in the tree hierarchy by dragging an article around.
     * When an article is moved, the script will send an rpc call to the server
     * and the drag-and-drop behavior will be deactivated while the request is pending.
     * - If the rpc call succeeds, the drag-and-drop behavior will be reactivated.
     * - If the rpc call fails, the change will be undo and the drag-and-drop
     *   behavior will be reactivated.
     * Unfortunately, `nestedSortable` can only restore one transformation. Disabling
     * the drag-and-drop behavior will ensure that the tree structure can be restored
     * if something went wrong.
     * 
     * ADSC TODO: Move to tree component
     */
    _setTreeListener() {
        const $sortable = $('.o_tree');
        $sortable.nestedSortable({
            axis: 'y',
            handle: '.o_article_handle',
            items: 'li',
            listType: 'ul',
            toleranceElement: '> div',
            opacity: 0.6,
            placeholder: 'ui-sortable-placeholder',
            tolerance: 'intersect',
            helper: 'clone',
            cursor: 'grabbing',
            cancel: '.readonly',
            scrollSpeed: 6,
            delay: 150,
            distance: 10,
            isTree: true,
            /**
             * Prevent a non-root shared article from becoming one, because the
             * access rights cannot be inferred in that case.
             *
             * @param {jQuery} placeholder jQuery element which represents the
             *                 destination position (may be the placeholder or
             *                 the nestedSortableItem if it already moved)
             * @param {jQuery} placeholderParent undefined, required by the
             *                 library (mjs.nestedSortable) function signature
             * @param {jQuery} currentItem jQuery nestedSortableItem element
             * @returns {boolean}
             */
            isAllowed: (placeholder, placeholderParent, currentItem) => {
                const section = currentItem.data('nestedSortableItem').offsetParent[0].parentElement;
                return (
                    // destination is under another article
                    placeholder[0].parentElement.closest('.o_article') ||
                    // destination is not within the shared section
                    !placeholder[0].closest('section[data-section="shared"]') ||
                    // starting position was a root of the shared section
                    section.getAttribute('data-section') === "shared"
                );
            },
            /**
             * Prevent the display of a placeholder in the root shared section
             * if the current item cannot be dragged there.
             *
             * @param {Event} event
             * @param {Object} ui
             */
            start: (event, ui) => {
                const section = ui.item.data('nestedSortableItem').offsetParent[0].parentElement;
                if (section.getAttribute('data-section') !== 'shared') {
                    $('section[data-section="shared"]').addClass('o_no_root_placeholder');
                }
            },
            /**
             * @param {Event} event
             * @param {Object} ui
             */
            stop: (event, ui) => {
                $sortable.sortable('disable');

                const $li = $(ui.item);
                const $section = $li.closest('section');
                const $parent = $li.parentsUntil('.o_tree', 'li');

                const data = {
                    article_id: $li.data('article-id'),
                    oldCategory: $li.data('category'),
                    newCategory: $section.data('section')
                };

                if ($parent.length > 0) {
                    data.target_parent_id = $parent.data('article-id');
                }
                const $next = $li.next();
                if ($next.length > 0) {
                    data.before_article_id = $next.data('article-id');
                }
                $li.siblings('.o_knowledge_empty_info').addClass('d-none');
                $('.o_knowledge_empty_info:only-child').removeClass('d-none');
                const confirmMove = async () => {
                    const id = $li.data('parent-id');
                    if (typeof id !== 'undefined') {
                        const $parent = $(`.o_tree .o_article[data-article-id="${id}"]`);
                        if (!$parent.children('ul').is(':parent')) {
                            $parent.removeClass('o_article_has_children');
                            this._removeUnfolded(id.toString());
                        }
                    }
                    if ($parent.length > 0) {
                        const $firstParent = $parent.first();
                        // Show other children if parent already had any
                        if ($firstParent.closest('.o_article').hasClass('o_article_has_children')) {
                            const $icon = $firstParent.find('> .o_article_handle > .o_article_caret > i');
                            if ($icon.hasClass("fa-caret-right")) {
                                const $ul = $firstParent.find('> div > ul');
                                if ($ul.length) {
                                    // Show children content stored in sibling
                                    $firstParent.find('> ul').prepend($ul.find('> li'));
                                    $ul.remove();
                                    this._addUnfolded($firstParent.data('article-id').toString());
                                    $icon.removeClass('fa-caret-right');
                                    $icon.addClass('fa-caret-down');
                                } else {
                                    // Fetch children (which includes li, so remove it)
                                    $li.detach();
                                    await this._fold($firstParent);
                                    if ($li.find('.o_article_handle').hasClass('o_article_active')) {
                                        const $newLi = $(`#article_${$li.data('article-id')}`);
                                        $newLi.find('.o_article_handle').addClass('o_article_active');
                                    }
                                    
                                }
                            }
                        } else {
                            this._addUnfolded($firstParent.data('article-id').toString());
                        }
                    }
                    $li.data('parent-id', $parent.data('article-id'));
                    $li.attr('data-parent-id', $parent.data('article-id'));
                    $li.data('category', data.newCategory);
                    $li.attr('data-category', data.newCategory);
                    let $children = $li.find('.o_article');
                    $children.each((_, child) => {
                        $(child).data('category', data.newCategory);
                        $(child).attr('data-category', data.newCategory);
                    });
                    const $sharedSection = $('section[data-section="shared"]');
                    if ($sharedSection.length) {
                        $sharedSection.removeClass('o_no_root_placeholder');
                        if (!$sharedSection.find('.o_article').length) {
                            $sharedSection.addClass('d-none');
                        }
                    }
                    $sortable.sortable('enable');
                };
                const rejectMove = () => {
                    /**
                     * When a move between two connected nestedSortable
                     * trees is canceled, more than one operation may be
                     * undone (library bug). To bypass sortable('cancel'),
                     * the last moved $item is returned at its original
                     * location (which may have to be restored too if it was
                     * cleaned), and a 'change' event is triggered from that
                     * rectified position for consistency (see the
                     * nestedSortable library).
                     */
                    const $item = ui.item.data('nestedSortableItem');
                    if ($item.domPosition.prev) {
                        // Restore $item position after its previous sibling
                        $item.domPosition.prev.after($item.currentItem[0]);
                    } else {
                        // Restore $item as the first child of the parent ul
                        $item.domPosition.parent.prepend($item.currentItem[0]);
                        if (!$item.domPosition.parent.parentElement) {
                            // The ul was cleaned from the document since it
                            // was empty, so it has to be restored too
                            const offsetParent = $item.offsetParent[0];
                            offsetParent.append($item.domPosition.parent);
                        }
                    }
                    // Hide caret if parent has no children after rejected move
                    const $firstParent = $parent.first();
                    if (!$firstParent.children('ul').is(':parent')) {
                        $firstParent.removeClass('mjs-nestedSortable-branch mjs-nestedSortable-expanded');
                        $firstParent.children('ul').remove();
                    }

                    // For consistency with the nestedSortable library,
                    // trigger the 'change' event from the moved $item
                    $item._trigger('change', null, $item._uiHash());
                    $('section[data-section="shared"]').removeClass('o_no_root_placeholder');
                    $sortable.sortable('enable');
                    $('.o_knowledge_empty_info').addClass('d-none');
                    $('.o_knowledge_empty_info:only-child').removeClass('d-none');
                };
                // The stop method may be called before the library calls
                // isAllowed (bug), so it has to be called again as a failsafe.
                if ($sortable.data('mjsNestedSortable').options.isAllowed(ui.item, undefined, ui.item)) {
                    this._moveArticle({...data,
                        onSuccess: confirmMove,
                        onReject: rejectMove,
                    });
                } else {
                    rejectMove();
                }
            },
        });
        // Allow drag and drop between sections:
        $('section[data-section="workspace"] .o_tree').nestedSortable(
            'option',
            'connectWith',
            'section[data-section="private"] .o_tree, section[data-section="shared"] .o_tree'
        );
        $('section[data-section="private"] .o_tree').nestedSortable(
            'option',
            'connectWith',
            'section[data-section="workspace"] .o_tree, section[data-section="shared"] .o_tree'
        );
        // connectWith both workspace and private sections:
        $('section[data-section="shared"] .o_tree').nestedSortable(
            'option',
            'connectWith',
            'section[data-section="workspace"] .o_tree, section[data-section="private"] .o_tree'
        );
    }

    /**
     * Unlike Bootstrap 4, Bootstrap 5 requires that the dropdown-menu be inside
     * a direct sibling of the dropdown toggle even though the real condition
     * technically is that the dropdown-menu and the dropdown toggle must have a
     * common ancestor with position: relative.
     *
     * In our case, we want to display a page blocker which will prevent the
     * user to drag an article while the emoji picker is open, along with the
     * emoji picker itself.
     *
     * To circumvent the harsher Bootstrap 5 limitation, the dropdown-menu
     * element is set manually if the default selector did not find it.
     *
     * @see bootstrap.dropdown._getMenuElement
     * @see bootstrap.selector-engine.next
     *
     * @private
     */
    _showEmojiPicker(ev) {
        if (!this.messaging || !this.messaging.knowledge) {
            return;
        }
        const articleId = Number(ev.target.closest('.o_article_emoji_dropdown').dataset.articleId) || this.resId;
        this.messaging.knowledge.update({
            currentArticle: { id: articleId },
            emojiPickerPopoverAnchorRef: { el: ev.target.closest('.o_article_emoji_dropdown .o_article_emoji') },
            emojiPickerPopoverView: {},
        });
    }

    _scrollToElement(container, element) {
        const rect = element.getBoundingClientRect();
        container.scrollTo(rect.left, rect.top);
    }
}

patch(KnowledgeArticleFormRenderer.prototype, "knowledge_article_form_renderer", KnowledgeTreePanelMixin);
KnowledgeArticleFormRenderer.components = {
    ...FormRenderer.components,
    PermissionPanel,
};
KnowledgeArticleFormRenderer.defaultProps = {
};
