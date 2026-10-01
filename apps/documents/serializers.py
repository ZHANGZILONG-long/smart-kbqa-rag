from pathlib import Path

from rest_framework import serializers

from accounts.models import Department
from documents.models import Document
from documents.services.parser import SUPPORTED_EXTENSIONS, detect_file_type


class DocumentSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(
        source='department.name', read_only=True, default=None
    )
    uploader_name = serializers.CharField(
        source='uploader.username', read_only=True, default=None
    )
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    visibility_display = serializers.CharField(
        source='get_visibility_display', read_only=True
    )

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
            'chunk_count',
            'error_message',
            'created_at',
            'updated_at',
        )
        read_only_fields = fields


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
        visibility = attrs.get('visibility', Document.Visibility.DEPARTMENT)
        department_id = attrs.get('department_id')

        if visibility == Document.Visibility.DEPARTMENT:
            if department_id:
                if not user.is_admin and user.department_id != department_id:
                    raise serializers.ValidationError(
                        {'department_id': '只能上传到自己的部门'}
                    )
                if not Department.objects.filter(pk=department_id).exists():
                    raise serializers.ValidationError(
                        {'department_id': '部门不存在'}
                    )
            elif not user.department_id:
                raise serializers.ValidationError(
                    {'department_id': '当前用户未分配部门，请指定 department_id 或改为全员可见'}
                )
        return attrs

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

        document = Document.objects.create(
            title=title,
            file=uploaded,
            original_filename=uploaded.name,
            file_type=detect_file_type(uploaded.name),
            file_size=uploaded.size,
            visibility=visibility,
            department=department,
            uploader=user,
            status=Document.Status.PENDING,
        )
        return document
