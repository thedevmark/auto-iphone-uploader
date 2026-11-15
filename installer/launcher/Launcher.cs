// Auto iPhone Uploader.exe: starts the app's own Python on launch_video_drop.py, which brings
// the server and the iPhone link up and opens the editor in its own window. Built by
// scripts\build_launcher.ps1 with the C# compiler that ships with Windows (.NET Framework 4).
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

static class Launcher
{
    const string Title = "Auto iPhone Uploader";

    [STAThread]
    static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(root, "launch_video_drop.py");
        if (!File.Exists(script))
            return Fail("launch_video_drop.py is missing from " + root + ". Reinstall Auto iPhone Uploader.");
        string python = FindPython(root);
        if (python == null)
            return Fail("Python was not found. Install Python 3.11 or newer from python.org with \"Add python.exe to PATH\" ticked, then open Auto iPhone Uploader again.");
        string extra = args.Length > 0 && args[0] == "--no-browser" ? " --no-browser" : "";
        try
        {
            Process.Start(new ProcessStartInfo(python, "\"" + script + "\"" + extra) { WorkingDirectory = root, UseShellExecute = false });
        }
        catch (Exception exc)
        {
            return Fail("Auto iPhone Uploader could not start Python (" + python + "): " + exc.Message);
        }
        return 0;
    }

    // The installer bundles its own Python; a checkout uses the one on PATH. The Microsoft
    // Store alias in WindowsApps only opens the Store, so it never counts.
    static string FindPython(string root)
    {
        string bundled = Path.Combine(root, "python", "pythonw.exe");
        if (File.Exists(bundled)) return bundled;
        foreach (string dir in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(';'))
        {
            if (dir.Trim().Length == 0 || dir.IndexOf("WindowsApps", StringComparison.OrdinalIgnoreCase) >= 0) continue;
            try
            {
                string candidate = Path.Combine(dir.Trim().Trim('"'), "pythonw.exe");
                if (File.Exists(candidate)) return candidate;
            }
            catch (ArgumentException) { }
        }
        return null;
    }

    static int Fail(string message)
    {
        MessageBox.Show(message, Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
        return 1;
    }
}
