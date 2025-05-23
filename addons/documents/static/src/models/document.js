/** @odoo-module **/

import { registerModel } from "@mail/model/model_core";
import { clear } from "@mail/model/model_field_command";
import { attr, one } from "@mail/model/model_field";

registerModel({
    name: "Document",
    recordMethods: {
        /**
         * Send the attachment for the browser to download.
         */
        download() {
            this.attachment.download();
        },
    },
    fields: {
        attachment: one("Attachment", {
            compute() {
                if (this.attachmentId) {
                    return {
                        id: this.attachmentId,
                        filename: this.name,
                        mimetype: this.mimetype,
                        url: this.url,
                    };
                }
                return clear();
            },
        }),
        attachmentId: attr(),
        attachmentViewerViewable: one("AttachmentViewerViewable", {
            /**
             * Default the attachmentViewerViewable to one linked to an attachment if it exists.
             */
            compute() {
                return {
                    documentOwner: this,
                };
            },
            isCausal: true,
        }),
        id: attr({
            identifying: true,
        }),
        defaultSource: attr({
            compute() {
                if (this.isImage) {
                    return `/web/image/${this.id}?model=documents.document&unique=${this.record.data.checksum}`;
                }
                if (this.isPdf) {
                    return (
                        "/web/static/lib/pdfjs/web/viewer.html?file=" +
                        encodeURIComponent(
                            `/web/content/${this.id}?model=documents.document&unique=${this.record.data.checksum}`
                        )
                    );
                }
                if (this.isUrlYoutube) {
                    const youtubeUrlMatch = this.url.match(
                        "youtu(?:.be|be.com)/(?:.*v(?:/|=)|(?:.*/)?)([a-zA-Z0-9-_]{11})"
                    );
                    const token = youtubeUrlMatch[1];
                    return `https://www.youtube.com/embed/${token}`;
                }
                return `/web/content/${this.id}?model=documents.document&unique=${this.record.data.checksum}`;
            },
        }),
        displayName: attr(),
        imageUrl: attr({
            compute() {
                return `/web/image/${this.id}?model=documents.document&unique=${this.record.data.checksum}`;
            },
        }),
        isImage: attr({
            compute() {
                if (this.attachment) {
                    return this.attachment.isImage;
                }
                const imageMimetypes = new Set([
                    "image/bmp",
                    "image/gif",
                    "image/jpeg",
                    "image/png",
                    "image/svg+xml",
                    "image/tiff",
                    "image/x-icon",
                ]);
                return imageMimetypes.has(this.mimetype);
            },
        }),
        isPdf: attr({
            compute() {
                return this.record.isPdf();
            },
        }),
        isText: attr({
            compute() {
                if (this.attachment) {
                    return this.attachment.isText;
                }
                const textMimeType = new Set(["application/javascript", "application/json", "text/css", "text/html", "text/plain"]);
                return textMimeType.has(this.mimetype);
            },
        }),
        isUrlYoutube: attr({
            compute() {
                if (this.attachment) {
                    return this.attachment.isUrlYoutube;
                }
                if (!this.url) {
                    return false;
                }
                return Boolean(this.url.match(
                    "youtu(?:.be|be.com)/(?:.*v(?:/|=)|(?:.*/)?)([a-zA-Z0-9-_]{11})"
                ));
            },
        }),
        isVideo: attr({
            compute() {
                if (this.attachment) {
                    return this.attachment.isVideo;
                }
                const videoMimeTypes = new Set(["audio/mpeg", "video/x-matroska", "video/mp4", "video/webm"]);
                return videoMimeTypes.has(this.mimetype);
            },
        }),
        isViewable: attr({
            compute() {
                if (this.record) {
                    return this.record.isViewable();
                }
                if (this.attachment) {
                    return this.attachment.isViewable;
                }
                return this.isText || this.isImage || this.isVideo || this.isPdf || this.isUrlYoutube;
            },
        }),
        mimetype: attr(),
        name: attr(),
        record: attr(),
        url: attr(),
    },
});
