from pathlib import Path

from rest_framework import serializers

from accounts.models import Department
from documents.models import Document
from documents.permissions import (
    can_approve_document,
    can_delete_document,
    can_reject_document,
    can_reprocess_document,
    can_upload_document,
)
from documents.services.parser import SUPPORTED_EXTENSIONS, detect_file_type


class DocumentSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(
        source='department.name', read_only=True, default=None
    )
    uploader_name = serializers.CharField(
        source='uploader.username', read_only=True, default=None
    )
    reviewed_by_name = serializers.CharField(
        source='reviewed_by.username', read_only=True, default=None
    )
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    visibility_display = serializers.CharField(
        source='get_visibility_display', read_only=True
    )
    can_delete = serializers.SerializerMethodField()
    can_reprocess = serializers.SerializerMethodField()
    can_approve = serializers.SerializerMethodField()
    can_reject = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = (
            'id',
            'title',
            'original_filename',
            'file_type',
            'file_size',
            'visibility',
            'visibility_display',
            'department',
            'department_name',
            'uploader',
            'uploader_name',
            'status',
            'status_display',
            'reviewed_by',
            'reviewed_by_name',
            'reviewed_at',
            'review_note',
            'chunk_count',
            'error_message',
            'can_delete',
            'can_reprocess',
            'can_approve',
            'can_reject',
            'created_at',
            'updated_at',
        )
        read_only_fields = fields

    def _user(self):
        request = self.context.get('request')
        return getattr(request, 'user', None) if request else None

    def get_can_delete(self, obj):
        return can_delete_document(self._user(), obj)

    def get_can_reprocess(self, obj):
        return can_reprocess_document(self._user(), obj)

    def get_can_approve(self, obj):
        return can_approve_document(self._user(), obj)

    def get_can_reject(self, obj):
        return can_reject_document(self._user(), obj)


class DocumentUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    title = serializers.CharField(required=False, allow_blank=True, max_length=255)
    visibility = serializers.ChoiceField(
        choices=Document.Visibility.choices,
        default=Document.Visibility.DEPARTMENT,
    )
    department_id = serializers.IntegerField(required=False, allow_null=True)

    def validate_file(self, value):
        ext = Path(value.name).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise serializers.ValidationError(
                f'仅支持 {", ".join(sorted(SUPPORTED_EXTENSIONS))} 格式'
            )
        max_mb = 20
        if value.size > max_mb * 1024 * 1024:
            raise serializers.ValidationError(f'文件不能超过 {max_mb}MB')
        return value

    def validate(self, attrs):
        request = self.context['request']
        user = request.user

        if not can_upload_document(user):
            raise serializers.ValidationError(
                '仅总管理员或部门管理员可以上传文档'
            )

        visibility = attrs.get('visibility', Document.Visibility.DEPARTMENT)
        department_id = attrs.get('department_id')

        # 全员可见文档：仅总管理员
        if visibility == Document.Visibility.PUBLIC:
            if not user.is_super_admin:
                raise serializers.ValidationError(
                    {'visibility': '仅总管理员可上传全员可见文档'}
                )
            return attrs

        # 部门文档
        if user.is_super_admin:
            if department_id is None:
                raise serializers.ValidationError(
                    {'department_id': '总管理员上传部门文档时必须指定 department_id'}
                )
            if not Department.objects.filter(pk=department_id).exists():
                raise serializers.ValidationError({'department_id': '部门不存在'})
            return attrs

        # 部门管理员：只能传本部门，上传后需总管理员审批
        if user.is_dept_admin:
            if not user.department_id:
                raise serializers.ValidationError(
                    '部门管理员未绑定部门，无法上传'
                )
            if department_id is None:
                attrs['department_id'] = user.department_id
            elif int(department_id) != int(user.department_id):
                raise serializers.ValidationError(
                    {'department_id': '部门管理员只能上传到自己的部门'}
                )
            if not Department.objects.filter(pk=attrs['department_id']).exists():
                raise serializers.ValidationError({'department_id': '部门不存在'})
            return attrs

        raise serializers.ValidationError('无权上传文档')

    def create(self, validated_data):
        request = self.context['request']
        user = request.user
        uploaded = validated_data['file']
        title = (validated_data.get('title') or '').strip() or Path(uploaded.name).stem
        visibility = validated_data.get(
            'visibility', Document.Visibility.DEPARTMENT
        )

        if visibility == Document.Visibility.PUBLIC:
            department = None
        else:
            dept_id = validated_data.get('department_id') or user.department_id
            department = Department.objects.filter(pk=dept_id).first()

        # 总管理员直接进入处理队列；部门管理员需审批
        if user.is_super_admin:
            initial_status = Document.Status.PENDING
        else:
            initial_status = Document.Status.PENDING_APPROVAL

        document = Document.objects.create(
            title=title,
            file=uploaded,
            original_filename=uploaded.name,
            file_type=detect_file_type(uploaded.name),
            file_size=uploaded.size,
            visibility=visibility,
            department=department,
            uploader=user,
            status=initial_status,
        )
        return document


class DocumentRejectSerializer(serializers.Serializer):
    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=500,
        default='',
    )
