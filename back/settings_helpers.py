"""Shared save-handlers for the settings page and the setup wizard.

`apply_setting_section` mirrors the old inline POST blocks of `settings_view`
so both the tabbed settings page and the first-run wizard can reuse one code
path (no duplicated form logic). Returns True when a known section was
handled, False otherwise.
"""

from django.contrib import messages


def apply_setting_section(user, request, section):
    from context.models import AgentIdentity, StoreConfig, BehaviorRules
    from back.models import Integration

    identity, _ = AgentIdentity.objects.get_or_create(user=user)
    store, _ = StoreConfig.objects.get_or_create(user=user)
    rules, _ = BehaviorRules.objects.get_or_create(user=user)
    integrations = list(Integration.objects.filter(user=user))

    if section == 'store':
        store.store_name = request.POST.get('store_name', '')
        store.address = request.POST.get('address', '')
        store.whatsapp_number = request.POST.get('whatsapp_number', '')
        store.delivery_charge_inside = request.POST.get('delivery_charge_inside') or 0
        store.delivery_charge_outside = request.POST.get('delivery_charge_outside') or 0
        store.support_open_time = request.POST.get('support_open_time') or '09:00'
        store.support_close_time = request.POST.get('support_close_time') or '21:00'
        store.timezone = request.POST.get('timezone') or 'Asia/Dhaka'
        store.currency = request.POST.get('currency') or 'BDT'
        store.save()
        messages.success(request, 'Store settings saved.')
        return True

    if section == 'agent':
        identity.name = request.POST.get('name') or 'Assistant'
        identity.role = request.POST.get('role', '')
        identity.tone = request.POST.get('tone') or 'friendly'
        identity.style = request.POST.get('style') or 'conversational'
        identity.language = request.POST.get('language') or 'en'
        if 'image' in request.FILES:
            identity.image = request.FILES['image']
        identity.save()
        messages.success(request, 'Agent identity saved.')
        return True

    if section == 'behavior':
        rules.greeting_message = request.POST.get('greeting_message', '')
        rules.custom_instructions = request.POST.get('custom_instructions', '')
        rules.chit_chat_enabled = 'chit_chat_enabled' in request.POST
        rules.chit_chat_style = request.POST.get('chit_chat_style') or 'moderate'
        rules.cross_sell_enabled = 'cross_sell_enabled' in request.POST
        rules.ask_open_ended = 'ask_open_ended' in request.POST
        rules.sample_questions_answers = request.POST.get('sample_questions_answers', '').strip()
        rules.save()
        messages.success(request, 'Behavior rules saved.')
        return True

    if section == 'knowledge':
        rules.knowledge_base = request.POST.get('knowledge_base', '').strip()
        rules.save(update_fields=['knowledge_base'])
        messages.success(request, 'Knowledge base saved.')
        return True

    if section == 'ai_model':
        for intg in integrations:
            intg.ai_model = request.POST.get(f'ai_model_{intg.pk}') or None
            intg.save(update_fields=['ai_model'])
        messages.success(request, 'AI model settings saved.')
        return True

    return False