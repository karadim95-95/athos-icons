from django import forms
from django.contrib import admin, messages
from django.contrib.admin.views.main import ALL_VAR, IS_FACETS_VAR, ORDER_VAR, PAGE_VAR
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join, linebreaks
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from catalog.admin import preview
from core.templatetags.shop import euro

from . import emails, services, tracking
from .models import CustomRequest, CustomRequestImage, Order, OrderEvent, OrderItem

STATUS_COLORS = {
    "pending": "#c27c0e", "awaiting_payment": "#2463a6", "paid": "#5b3fa0", "shipped": "#127a7a",
    "in_transit": "#127a7a", "delivered": "#2f7d32", "rejected": "#8b8b8b", "cancelled": "#8b8b8b",
    "new": "#c27c0e", "discussion": "#2463a6", "quoted": "#5b3fa0", "accepted": "#2f7d32",
    "declined": "#8b8b8b", "closed": "#8b8b8b",
}


def status_badge(obj):
    return format_html('<span style="background:{};color:#fff;padding:3px 8px;border-radius:10px;'
                       'white-space:nowrap;font-size:11px">{}</span>',
                       STATUS_COLORS.get(obj.status, "#666"), obj.get_status_display())


def render_thread(thread):
    msgs = thread.messages.select_related("author").all()
    if not msgs:
        return _("No messages yet.")
    rows = [
        ("#eef3fa" if m.from_shop else "#fdf3e1",
         _("You") if m.from_shop else thread.user,
         timezone.localtime(m.created_at).strftime("%d/%m/%Y %H:%M"),
         mark_safe(linebreaks(m.body, autoescape=True)))
        for m in msgs
    ]
    return format_html_join(
        "", '<div style="margin:0 0 10px;padding:8px 12px;border-radius:8px;max-width:640px;background:{}">'
            '<small style="opacity:.7">{} · {}</small>{}</div>',
        rows,
    )


class UnreadFilter(admin.SimpleListFilter):
    title = _("messages")
    parameter_name = "unread"

    def lookups(self, request, model_admin):
        return [("1", _("Unanswered messages"))]

    def queryset(self, request, queryset):
        if self.value() == "1":
            return queryset.filter(messages__from_shop=False, messages__read_at__isnull=True).distinct()
        return queryset


class ArchiveFilter(admin.SimpleListFilter):
    """The order list opens on active orders. Searching or filtering looks in the archive too."""

    title = _("archive")
    parameter_name = "archive"

    def lookups(self, request, model_admin):
        return [("archived", _("Archived")), ("all", _("All orders"))]

    def choices(self, changelist):
        choices = list(super().choices(changelist))
        choices[0]["display"] = _("Active")
        return choices

    def queryset(self, request, queryset):
        if self.value() == "archived":
            return queryset.filter(archived_at__isnull=False)
        browsing = {ORDER_VAR, PAGE_VAR, ALL_VAR, IS_FACETS_VAR, self.parameter_name}
        if self.value() == "all" or any(value for key, value in request.GET.items() if key not in browsing):
            return queryset
        return queryset.filter(archived_at__isnull=True)


class TrackingProblemFilter(admin.SimpleListFilter):
    title = _("delivery")
    parameter_name = "tracking"

    def lookups(self, request, model_admin):
        return [("problem", _("Delivery problems"))]

    def queryset(self, request, queryset):
        if self.value() == "problem":
            return queryset.with_tracking_problem()
        return queryset


class ReplyFields(forms.ModelForm):
    reply = forms.CharField(
        label=_("Send a message to the customer"), required=False,
        widget=forms.Textarea(attrs={"rows": 3, "cols": 80}),
        help_text=_("Emailed to the customer and shown in their account. They can reply from there."),
    )


class ThreadAdminMixin:
    @admin.display(description=_("conversation"))
    def conversation(self, obj):
        return render_thread(obj) if obj.pk else "—"

    @admin.display(description=_("customer account"))
    def customer_link(self, obj):
        url = reverse("admin:accounts_user_change", args=[obj.user_id])
        badge = _("business") if obj.is_business else _("individual")
        return format_html('<a href="{}">{}</a> · {} · <a href="mailto:{}">{}</a>',
                           url, obj.user, badge, obj.user.email, obj.user.email)

    def send_reply(self, request, obj, form):
        body = form.cleaned_data.get("reply", "").strip()
        if body:
            services.post_shop_message(obj, request.user, body)
            messages.success(request, _("Your message was sent to the customer."))


# --- Orders ------------------------------------------------------------------------
class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = ["product_name", "product", "quantity", "unit_price", "line_total_display"]
    readonly_fields = ["line_total_display"]
    autocomplete_fields = ["product"]

    @admin.display(description=_("total"))
    def line_total_display(self, obj):
        return euro(obj.line_total) if obj.pk else "—"


class OrderEventInline(admin.TabularInline):
    model = OrderEvent
    extra = 0
    can_delete = False
    fields = ["created_at", "status", "note", "emailed"]
    readonly_fields = fields
    verbose_name_plural = _("status history")

    def has_add_permission(self, request, obj=None):
        return False


class OrderAdminForm(ReplyFields):
    notify_customer = forms.BooleanField(
        label=_("Email the customer about a status change"), required=False, initial=True,
    )
    status_note = forms.CharField(
        label=_("Note for the status email"), required=False, widget=forms.Textarea(attrs={"rows": 2, "cols": 80}),
        help_text=_("Optional, e.g. why you declined, or 'we added the shipping cost'. "
                    "Shown in the email and in the customer's order history."),
    )

    class Meta:
        model = Order
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if tracking.enabled() and "courier" in self.fields:
            self.fields["courier"].help_text = _(
                "ACS and Geniki Taxydromiki parcels are tracked automatically: the order moves to In transit and "
                "Delivered by itself and the customer is emailed each time.")

    def clean(self):
        data = super().clean()
        if data.get("status") in Order.ON_THE_WAY and not (data.get("tracking_number") or data.get("tracking_url")):
            self.add_error("tracking_number", _("Add a tracking number or link so the customer can follow the parcel."))
        # The courier's tracking page, unless you entered another link yourself.
        previous = tracking.tracking_url(self.initial.get("courier"), self.initial.get("tracking_number"))
        if data.get("tracking_number") and data.get("tracking_url", "") in ("", previous):
            data["tracking_url"] = tracking.tracking_url(data.get("courier"), data["tracking_number"]) or \
                data.get("tracking_url", "")
        return data


@admin.register(Order)
class OrderAdmin(ThreadAdminMixin, admin.ModelAdmin):
    form = OrderAdminForm
    inlines = [OrderItemInline, OrderEventInline]
    list_display = ["number", "created_at", "customer", "type_badge", "status_display", "total_display"]
    list_filter = [ArchiveFilter, "status", "is_business", UnreadFilter, TrackingProblemFilter, "created_at"]
    search_fields = ["number", "full_name", "email", "company_name", "phone", "vat_number"]
    date_hierarchy = "created_at"
    list_select_related = ["user"]
    actions = ["accept", "mark_paid", "mark_shipped", "mark_delivered", "check_tracking", "archive", "unarchive"]
    readonly_fields = ["number", "customer_link", "created_at", "archived_at", "totals", "customer_notes",
                       "conversation", "custom_request_link", "courier_tracking"]
    fieldsets = [
        (_("Status"), {"fields": [("number", "created_at", "archived_at"), "status", "notify_customer", "status_note",
                                  ("courier", "tracking_number"), "tracking_url", "courier_tracking"]}),
        (_("Customer"), {"fields": ["customer_link", ("full_name", "email", "phone"),
                                    ("company_name", "vat_number", "tax_office"), "customer_notes",
                                    "custom_request_link"]}),
        (_("Shipping address"), {"fields": ["address", ("city", "postal_code", "country")]}),
        (_("Amounts"), {"fields": ["shipping_cost", ("prices_include_vat", "vat_rate"), "totals"],
                        "description": _("Change item prices or shipping below before accepting. "
                                         "Totals update when you save.")}),
        (_("Conversation"), {"fields": ["conversation", "reply"]}),
        (_("Private"), {"fields": ["internal_notes"]}),
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("items")

    @admin.display(description=_("customer"), ordering="full_name")
    def customer(self, obj):
        return obj.company_name or obj.full_name

    @admin.display(description=_("type"), ordering="is_business")
    def type_badge(self, obj):
        return _("Business") if obj.is_business else _("Individual")

    @admin.display(description=_("status"), ordering="status")
    def status_display(self, obj):
        if obj.archived_at:
            return format_html('{} <small style="opacity:.7">{}</small>', status_badge(obj), _("archived"))
        return status_badge(obj)

    @admin.display(description=_("total"))
    def total_display(self, obj):
        return euro(obj.total)

    @admin.display(description=_("totals"))
    def totals(self, obj):
        if not obj.pk:
            return "—"
        basis = _("incl. VAT") if obj.prices_include_vat else _("excl. VAT")
        return format_html(
            "{}: {} ({})<br>{}: {}<br>{}: {}<br>{} {}%: {}<br><b>{}: {}</b>",
            _("Items"), euro(obj.items_total), basis, _("Shipping"), euro(obj.shipping_cost),
            _("Net amount"), euro(obj.net_total), _("VAT"), obj.vat_rate.normalize(), euro(obj.vat_amount),
            _("Total to pay"), euro(obj.total),
        )

    @admin.display(description=_("custom request"))
    def custom_request_link(self, obj):
        if not obj.custom_request_id:
            return "—"
        url = reverse("admin:orders_customrequest_change", args=[obj.custom_request_id])
        return format_html('<a href="{}">{}</a>', url, obj.custom_request)

    @admin.display(description=_("automatic tracking"))
    def courier_tracking(self, obj):
        if not tracking.courier_for(obj):
            if obj.courier in tracking.COURIERS:
                return _("Off for %(courier)s: its API details are not set yet (see README).") % {
                    "courier": obj.get_courier_display()}
            return _("Automatic for ACS and Geniki Taxydromiki: choose the courier and enter the tracking number.")
        if obj.tracking_error:
            return format_html('<span style="color:#b3261e">{}</span>', obj.tracking_error)
        if not obj.tracking_checked_at:
            return _("Checked when you save the order as Shipped, then every 2 hours.")
        when = timezone.localtime(obj.tracking_event_at).strftime("%d/%m/%Y %H:%M") if obj.tracking_event_at else ""
        return format_html(
            "<b>{}</b><br>{}{}<br><small>{} {}</small>",
            obj.get_tracking_status_display() or "—", obj.tracking_event, f" ({when})" if when else "",
            _("Last checked:"), timezone.localtime(obj.tracking_checked_at).strftime("%d/%m/%Y %H:%M"),
        )

    def track_now(self, request, order, new_parcel):
        if new_parcel:  # forget what the courier said about the previous number
            order.tracking_status = order.tracking_event = order.tracking_error = ""
            order.tracking_event_at = order.tracking_checked_at = None
        moved = tracking.check(order)
        if order.tracking_error:
            messages.warning(request, _("The courier could not track this parcel yet: %(error)s. "
                                        "It will try again every 2 hours.") % {"error": order.tracking_error})
        elif moved:
            messages.success(request, _("The courier already reports: %(stage)s. The customer was emailed.") % {
                "stage": order.get_tracking_status_display()})
        else:
            messages.success(request, _("Automatic tracking is on: the order will move to In transit and "
                                        "Delivered by itself."))

    def save_related(self, request, form, formsets, change):
        # Items are saved here, so emails sent below already show the updated prices.
        super().save_related(request, form, formsets, change)
        order = Order.objects.get(pk=form.instance.pk)  # fresh copy: the edited items, not a cached list
        if not change or "status" in form.changed_data:
            notify = form.cleaned_data.get("notify_customer")
            event = services.record_status(order, form.cleaned_data.get("status_note", ""), notify)
            if notify and not event.emailed:
                messages.warning(request, _("Status saved, but the email could not be sent. Check the email settings."))
            elif notify:
                messages.success(request, _("The customer was emailed: %(status)s") % {
                    "status": order.get_status_display()})
        if order.archived_at and "status" in form.changed_data:
            messages.info(request, _("The order was moved to the archive. You can find it with the search box "
                                     "or the Archive filter."))
        new_parcel = bool({"courier", "tracking_number"} & set(form.changed_data))
        if tracking.courier_for(order) and order.status in Order.ON_THE_WAY and (new_parcel or "status" in form.changed_data):
            self.track_now(request, order, new_parcel)
        self.send_reply(request, order, form)

    def _bulk_status(self, request, queryset, status, allowed_from):
        done = 0
        for order in queryset.filter(status__in=allowed_from):
            services.set_order_status(order, status)
            done += 1
        skipped = queryset.count() - done
        self.message_user(request, _("%(n)s order(s) updated and customers emailed.") % {"n": done}, messages.SUCCESS)
        if skipped:
            self.message_user(request, _("%(n)s order(s) skipped because their status did not fit.") % {"n": skipped},
                              messages.WARNING)

    @admin.action(description=_("Accept → email payment details"))
    def accept(self, request, queryset):
        self._bulk_status(request, queryset, Order.Status.AWAITING_PAYMENT, [Order.Status.PENDING])

    @admin.action(description=_("Payment received"))
    def mark_paid(self, request, queryset):
        self._bulk_status(request, queryset, Order.Status.PAID, [Order.Status.AWAITING_PAYMENT])

    @admin.action(description=_("Shipped (only orders with tracking info)"))
    def mark_shipped(self, request, queryset):
        queryset = queryset.exclude(tracking_number="", tracking_url="")
        self._bulk_status(request, queryset, Order.Status.SHIPPED, [Order.Status.PAID])

    @admin.action(description=_("Delivered"))
    def mark_delivered(self, request, queryset):
        self._bulk_status(request, queryset, Order.Status.DELIVERED, Order.ON_THE_WAY)
        self.message_user(request, _("Delivered orders were moved to the archive."), messages.INFO)

    @admin.action(description=_("Move to the archive"))
    def archive(self, request, queryset):
        n = queryset.filter(archived_at__isnull=True).update(archived_at=timezone.now())
        self.message_user(request, _("%(n)s order(s) moved to the archive.") % {"n": n}, messages.SUCCESS)

    @admin.action(description=_("Bring back from the archive"))
    def unarchive(self, request, queryset):
        n = queryset.filter(archived_at__isnull=False).update(archived_at=None)
        self.message_user(request, _("%(n)s order(s) brought back to the active list.") % {"n": n},
                          messages.SUCCESS)

    @admin.action(description=_("Check the couriers now"))
    def check_tracking(self, request, queryset):
        if not tracking.enabled():
            self.message_user(request, _("Automatic tracking is off: add the ACS or Geniki Taxydromiki API details "
                                         "(see README)."), messages.WARNING)
            return
        moved = tracking.sync(queryset)
        self.message_user(request, _("Couriers checked. %(n)s order(s) moved on and their customers were emailed.")
                          % {"n": moved}, messages.SUCCESS)


# --- Custom requests ---------------------------------------------------------------
class CustomRequestImageInline(admin.TabularInline):
    model = CustomRequestImage
    extra = 0
    fields = ["picture"]
    readonly_fields = ["picture"]
    verbose_name_plural = _("reference photos from the customer")

    @admin.display(description="")
    def picture(self, obj):
        return format_html('<a href="{}" target="_blank">{}</a>', obj.image.url, preview(obj.thumb_url, 140))

    def has_add_permission(self, request, obj=None):
        return False


class CustomRequestAdminForm(ReplyFields):
    send_offer = forms.BooleanField(
        label=_("Send this offer to the customer now"), required=False,
        help_text=_("Emails the price to the customer. They can accept it from their account, "
                    "which creates an order and sends them the payment details."),
    )

    class Meta:
        model = CustomRequest
        fields = "__all__"

    def clean(self):
        data = super().clean()
        if data.get("send_offer") and data.get("quoted_price") is None:
            self.add_error("quoted_price", _("Enter the offer price first."))
        return data


@admin.register(CustomRequest)
class CustomRequestAdmin(ThreadAdminMixin, admin.ModelAdmin):
    form = CustomRequestAdminForm
    inlines = [CustomRequestImageInline]
    list_display = ["number", "created_at", "user", "subject", "quantity", "status_display", "quoted_price"]
    list_filter = ["status", "is_business", UnreadFilter]
    search_fields = ["number", "subject", "description", "user__email", "user__last_name", "user__company_name"]
    list_select_related = ["user"]
    readonly_fields = ["number", "created_at", "customer_link", "subject", "description", "dimensions", "quantity",
                       "budget", "needed_by", "conversation", "order_link"]
    fieldsets = [
        (_("Request"), {"fields": [("number", "created_at"), "status", "customer_link", "subject", "description",
                                   ("dimensions", "quantity"), ("budget", "needed_by")]}),
        (_("Your offer"), {"fields": [("quoted_price", "quoted_shipping"), "quote_note", "send_offer", "order_link"],
                           "description": _("For individuals the price includes VAT; for businesses it excludes VAT.")}),
        (_("Conversation"), {"fields": ["conversation", "reply"]}),
        (_("Private"), {"fields": ["internal_notes"]}),
    ]

    def has_add_permission(self, request):
        return False

    @admin.display(description=_("status"), ordering="status")
    def status_display(self, obj):
        return status_badge(obj)

    @admin.display(description=_("order"))
    def order_link(self, obj):
        order = getattr(obj, "order", None)
        if not order:
            return "—"
        return format_html('<a href="{}">{}</a>', reverse("admin:orders_order_change", args=[order.pk]), order)

    def save_model(self, request, obj, form, change):
        if form.cleaned_data.get("send_offer"):
            obj.status = CustomRequest.Status.QUOTED
        super().save_model(request, obj, form, change)
        if form.cleaned_data.get("send_offer"):
            if emails.request_status(obj):
                messages.success(request, _("The offer was emailed to the customer."))
            else:
                messages.warning(request, _("Offer saved, but the email could not be sent."))
        elif "status" in form.changed_data and obj.status in (CustomRequest.Status.DECLINED,
                                                              CustomRequest.Status.CLOSED):
            emails.request_status(obj)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        self.send_reply(request, form.instance, form)
