from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import ValidationError
from django import forms
from .models import *
from .forms import SiteConfigForm

admin.ModelAdmin.list_per_page=10
admin.site.site_header='成长工坊 · 系统管理'
admin.site.site_title='成长工坊'
admin.site.index_title='账号、身份与系统配置（家庭业务请进入家长端）'

@admin.register(User)
class AccountAdmin(UserAdmin):
    fieldsets=UserAdmin.fieldsets+(("业务身份",{'fields':('is_student','is_parent','reduce_motion')}),)
    add_fieldsets=UserAdmin.add_fieldsets+(("业务身份",{'fields':('is_student','is_parent')}),)
    list_display=['username','first_name','is_student','is_parent','is_staff','is_active']

@admin.register(ParentChild)
class BindingAdmin(admin.ModelAdmin):
    list_display=['parent','child']
    def formfield_for_foreignkey(self,db_field,request,**kwargs):
        if db_field.name=='parent': kwargs['queryset']=User.objects.filter(is_parent=True)
        if db_field.name=='child': kwargs['queryset']=User.objects.filter(is_student=True)
        return super().formfield_for_foreignkey(db_field,request,**kwargs)

admin.site.register(Identity)
admin.site.register(Subject)

@admin.register(SiteConfig)
class ConfigAdmin(admin.ModelAdmin):
    form=SiteConfigForm
    def save_model(self,request,obj,form,change):
        from .resources import upload_asset
        image=form.cleaned_data.get('logo_upload')
        if image:
            obj.logo=upload_asset(request.user,image,category='brand')
        super().save_model(request,obj,form,change)
    def has_add_permission(self,request): return not SiteConfig.objects.exists()
    def has_delete_permission(self,request,obj=None): return False

@admin.register(Animation)
class AnimationAdmin(admin.ModelAdmin):
    list_display=['name','scene','theme','enabled']

@admin.register(Asset)
class MaterialAdmin(admin.ModelAdmin):
    list_display=['name','category','mime','size','created_at']
    readonly_fields=['id','task','purpose','file','name','mime','size','draft_active','created_at']
    def get_queryset(self,request): return super().get_queryset(request).exclude(category='attachment')
    def has_add_permission(self,request): return False
    def has_delete_permission(self,request,obj=None): return False

@admin.register(AIRun)
class RunAdmin(admin.ModelAdmin):
    def get_queryset(self,request): return super().get_queryset(request).defer('request','response','error')
    list_display=['id','job','attempt','actual_model','created_at']
    list_per_page=10
    readonly_fields=[f.name for f in AIRun._meta.fields]
    def has_add_permission(self,request): return False
    def has_delete_permission(self,request,obj=None): return False

@admin.register(ReviewJob)
class JobAdmin(admin.ModelAdmin):
    list_display=['id','submission','status','attempts','created_at']
    readonly_fields=[f.name for f in ReviewJob._meta.fields]
    list_per_page=10
    def has_add_permission(self,request): return False
    def has_delete_permission(self,request,obj=None): return False
