from django.contrib import admin

from documents.models import Document, DocumentChunk


class DocumentChunkInline(admin.TabularInline):
    model = DocumentChunk
    extra = 0
    readonly_fields = ('chunk_index', 'chroma_id', 'content', 'created_at')
    can_delete = False


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'title',
        'visibility',
        'department',
        'status',
        'chunk_count',
        'uploader',
        'created_at',
    )
    list_filter = ('status', 'visibility', 'file_type', 'department')
    search_fields = ('title', 'original_filename')
    inlines = [DocumentChunkInline]
    readonly_fields = (
        'original_filename',
        'file_type',
        'file_size',
        'chunk_count',
        'error_message',
        'created_at',
        'updated_at',
    )


@admin.register(DocumentChunk)
class DocumentChunkAdmin(admin.ModelAdmin):
    list_display = ('id', 'document', 'chunk_index', 'chroma_id', 'created_at')
    search_fields = ('chroma_id', 'content')
