from django.core.management.base import BaseCommand

from documents.models import Document
from documents.tasks import process_document_task


class Command(BaseCommand):
    help = '重新解析并向量化全部（或指定）文档，切换 embedding 后应执行一次。'

    def add_arguments(self, parser):
        parser.add_argument(
            '--id',
            type=int,
            action='append',
            dest='ids',
            help='只处理指定文档 ID，可重复传入',
        )
        parser.add_argument(
            '--status',
            choices=['all', 'ready', 'failed', 'pending'],
            default='all',
            help='按状态筛选（默认 all）',
        )

    def handle(self, *args, **options):
        qs = Document.objects.all().order_by('id')
        ids = options.get('ids')
        if ids:
            qs = qs.filter(id__in=ids)
        status = options.get('status') or 'all'
        if status != 'all':
            qs = qs.filter(status=status)

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.WARNING('没有需要处理的文档'))
            return

        self.stdout.write(f'开始重处理 {total} 个文档…')
        ok = 0
        fail = 0
        for doc in qs.iterator():
            result = process_document_task(doc.id)
            doc.refresh_from_db()
            if result.get('ok') and doc.status == Document.Status.READY:
                ok += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f'  #{doc.id} ready chunks={doc.chunk_count}'
                    )
                )
            else:
                fail += 1
                self.stdout.write(
                    self.style.ERROR(
                        f'  #{doc.id} failed: {doc.error_message or result}'
                    )
                )
        self.stdout.write(
            self.style.NOTICE(f'完成：成功 {ok}，失败 {fail}，合计 {total}')
        )
