# Generated manually for AuditLog
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AuditLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('username', models.CharField(blank=True, max_length=150, verbose_name='用户名快照')),
                ('action', models.CharField(choices=[('login', '登录'), ('logout', '退出'), ('register', '注册'), ('change_password', '修改密码'), ('upload_doc', '上传文档'), ('approve_doc', '审批通过'), ('reject_doc', '审批驳回'), ('delete_doc', '删除文档'), ('reprocess_doc', '重处理文档'), ('ask', '知识问答'), ('staff_update', '更新员工'), ('other', '其他')], db_index=True, max_length=32, verbose_name='动作')),
                ('object_type', models.CharField(blank=True, max_length=64, verbose_name='对象类型')),
                ('object_id', models.CharField(blank=True, max_length=64, verbose_name='对象ID')),
                ('detail', models.JSONField(blank=True, default=dict, verbose_name='详情')),
                ('ip', models.GenericIPAddressField(blank=True, null=True, verbose_name='IP')),
                ('path', models.CharField(blank=True, max_length=255, verbose_name='路径')),
                ('method', models.CharField(blank=True, max_length=16, verbose_name='方法')),
                ('status_code', models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='状态码')),
                ('success', models.BooleanField(default=True, verbose_name='是否成功')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='时间')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='audit_logs', to=settings.AUTH_USER_MODEL, verbose_name='操作者')),
            ],
            options={
                'verbose_name': '审计日志',
                'verbose_name_plural': '审计日志',
                'ordering': ['-created_at'],
            },
        ),
    ]
