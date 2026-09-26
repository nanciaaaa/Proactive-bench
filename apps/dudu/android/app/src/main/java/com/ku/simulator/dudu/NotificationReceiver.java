package com.ku.simulator.dudu;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.media.AudioAttributes;
import android.provider.Settings;

public class NotificationReceiver extends BroadcastReceiver {
    private static final String ACTION = "com.ku.simulator.dudu.PUBLISH_NOTIFICATION";
    private static final String CANCEL_ACTION = "com.ku.simulator.dudu.CANCEL_NOTIFICATION";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (CANCEL_ACTION.equals(intent.getAction())) {
            String notificationId = intent.getStringExtra("notification_id");
            if (notificationId != null) {
                NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
                manager.cancel(notificationId.hashCode());
            }
            return;
        }
        if (!ACTION.equals(intent.getAction())) return;
        String notificationId = intent.getStringExtra("notification_id");
        String channelId = intent.getStringExtra("channel");
        String title = intent.getStringExtra("title");
        String body = intent.getStringExtra("body");
        if (notificationId == null || channelId == null || title == null || body == null) return;
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(channelId, channelId,
                    NotificationManager.IMPORTANCE_HIGH);
            channel.setSound(Settings.System.DEFAULT_NOTIFICATION_URI,
                    new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION).build());
            manager.createNotificationChannel(channel);
        }
        Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(context, channelId)
                : new Notification.Builder(context);
        Intent launch = context.getPackageManager().getLaunchIntentForPackage(context.getPackageName());
        PendingIntent contentIntent = launch == null ? null : PendingIntent.getActivity(context, 0, launch,
                PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M
                        ? PendingIntent.FLAG_IMMUTABLE : 0));
        Notification notification = builder.setSmallIcon(R.drawable.ic_stat_notification)
                .setContentTitle(title).setContentText(body).setStyle(new Notification.BigTextStyle().bigText(body))
                .setWhen(System.currentTimeMillis()).setShowWhen(true).setAutoCancel(true)
                .setCategory(Notification.CATEGORY_EVENT)
                .setPriority(Notification.PRIORITY_HIGH).setDefaults(Notification.DEFAULT_ALL)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setContentIntent(contentIntent).build();
        manager.notify(notificationId.hashCode(), notification);
    }
}
