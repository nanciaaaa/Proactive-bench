import java.awt.Color;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;
import java.io.File;
import javax.imageio.ImageIO;

public class GenerateBrandAssets {
  private static BufferedImage resize(BufferedImage input, int width, int height) {
    BufferedImage output = new BufferedImage(width, height, BufferedImage.TYPE_INT_ARGB);
    var graphics = output.createGraphics();
    graphics.setRenderingHint(RenderingHints.KEY_INTERPOLATION, RenderingHints.VALUE_INTERPOLATION_BICUBIC);
    graphics.setRenderingHint(RenderingHints.KEY_RENDERING, RenderingHints.VALUE_RENDER_QUALITY);
    graphics.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
    graphics.drawImage(input, 0, 0, width, height, null);
    graphics.dispose();
    return output;
  }

  private static void write(BufferedImage image, File target) throws Exception {
    target.getParentFile().mkdirs();
    ImageIO.write(image, "png", target);
  }

  public static void main(String[] args) throws Exception {
    if (args.length != 3) throw new IllegalArgumentException("icon.png #RRGGBB android-res-dir");
    BufferedImage icon = ImageIO.read(new File(args[0]));
    File res = new File(args[2]);
    String[] density = {"mdpi", "hdpi", "xhdpi", "xxhdpi", "xxxhdpi"};
    int[] launcher = {48, 72, 96, 144, 192};
    int[] foreground = {108, 162, 216, 324, 432};
    for (int i = 0; i < density.length; i++) {
      File folder = new File(res, "mipmap-" + density[i]);
      write(resize(icon, launcher[i], launcher[i]), new File(folder, "ic_launcher.png"));
      write(resize(icon, launcher[i], launcher[i]), new File(folder, "ic_launcher_round.png"));
      write(resize(icon, foreground[i], foreground[i]), new File(folder, "ic_launcher_foreground.png"));
    }
    Color background = Color.decode(args[1]);
    File[] splashFolders = res.listFiles(file -> file.isDirectory() && file.getName().startsWith("drawable"));
    if (splashFolders == null) return;
    for (File folder : splashFolders) {
      File target = new File(folder, "splash.png");
      if (!target.isFile()) continue;
      BufferedImage current = ImageIO.read(target);
      BufferedImage splash = new BufferedImage(current.getWidth(), current.getHeight(), BufferedImage.TYPE_INT_RGB);
      var graphics = splash.createGraphics();
      graphics.setColor(background);
      graphics.fillRect(0, 0, splash.getWidth(), splash.getHeight());
      int size = Math.max(96, Math.min(splash.getWidth(), splash.getHeight()) * 28 / 100);
      BufferedImage mark = resize(icon, size, size);
      graphics.drawImage(mark, (splash.getWidth() - size) / 2, (splash.getHeight() - size) / 2, null);
      graphics.dispose();
      write(splash, target);
    }
  }
}
