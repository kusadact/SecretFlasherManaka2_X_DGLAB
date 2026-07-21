using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Net.Sockets;
using System.Net.WebSockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using BepInEx;
using BepInEx.Configuration;
using BepInEx.Logging;
using BepInEx.Unity.IL2CPP;
using ExposureUnnoticed2.Master.AdultGoods;
using ExposureUnnoticed2.Object3D.AdultGoods;
using ExposureUnnoticed2.Object3D.NPC.Script;
using ExposureUnnoticed2.Object3D.Player.Scripts;
using ExposureUnnoticed2.Object3D.Player.Scripts.Costume;
using ExposureUnnoticed2.Scripts.InGame;
using HarmonyLib;

namespace SecretFlasherManakaCoyoteLink
{
    [BepInPlugin("secretflashermanaka.coyotelink.vibrator", "Secret Flasher Manaka Coyote Link", "0.1.0")]
    public sealed class Plugin : BasePlugin
    {
        private Harmony harmony;
        internal static ManualLogSource Logger;

        internal static ConfigEntry<bool> Armed;
        internal static ConfigEntry<bool> DryRun;
        internal static ConfigEntry<string> OutputMode;
        internal static ConfigEntry<string> UdpHost;
        internal static ConfigEntry<int> UdpPort;
        internal static ConfigEntry<string> WebSocketUrl;
        internal static ConfigEntry<string> WebSocketTemplate;
        internal static ConfigEntry<string> Channel;
        internal static ConfigEntry<float> SendHz;
        internal static ConfigEntry<float> SuspicionDeadZone;
        internal static ConfigEntry<float> SuspicionAtMax;
        internal static ConfigEntry<float> MaxIntensityPercent;
        internal static ConfigEntry<float> FoundBoostPercent;
        internal static ConfigEntry<float> MaxChangePercentPerSecond;
        internal static ConfigEntry<bool> ZeroWhenNoNpc;
        internal static ConfigEntry<float> VibratorLowIntensityPercent;
        internal static ConfigEntry<float> VibratorHighIntensityPercent;
        internal static ConfigEntry<float> VibratorRandomFallbackPercent;
        internal static ConfigEntry<float> ClimaxGaugeMax;
        internal static ConfigEntry<float> ClimaxScalePercent;
        internal static ConfigEntry<float> PistonWeakIntensityPercent;
        internal static ConfigEntry<float> PistonMediumIntensityPercent;
        internal static ConfigEntry<float> PistonStrongIntensityPercent;
        internal static ConfigEntry<float> PistonRandomFallbackPercent;
        internal static ConfigEntry<string> ToggleKey;
        internal static ConfigEntry<string> PanicKey;
        internal static ConfigEntry<float> LogIntervalSeconds;

        public override void Load()
        {
            Logger = Log;

            Armed = Config.Bind("Safety", "Armed", true, "Master enable. Press the panic key or toggle key to disable output immediately.");
            DryRun = Config.Bind("Safety", "DryRun", false, "When true, the plugin only logs computed intensity and never sends device output.");
            MaxIntensityPercent = Config.Bind("Safety", "MaxIntensityPercent", 20f, "Hard cap for output intensity, expressed as percent. Start low.");
            MaxChangePercentPerSecond = Config.Bind("Safety", "MaxChangePercentPerSecond", 20f, "Ramp limiter. The output cannot change faster than this percent per second.");
            ZeroWhenNoNpc = Config.Bind("Safety", "ZeroWhenNoNpc", true, "Force output to zero when no NPC data is available.");
            VibratorLowIntensityPercent = Config.Bind("Vibrator", "LowIntensityPercent", 30f, "Coyote intensity used while the game's effective vibrator mode is Low.");
            VibratorHighIntensityPercent = Config.Bind("Vibrator", "HighIntensityPercent", 70f, "Coyote intensity used while the game's effective vibrator mode is High.");
            VibratorRandomFallbackPercent = Config.Bind("Vibrator", "RandomFallbackPercent", 50f, "Fallback intensity only if Random has not produced a Low/High currentMode yet.");
            ClimaxGaugeMax = Config.Bind("Climax", "GaugeMax", 100f, "Maximum value of the game's PlayerEcstasy gauge.");
            ClimaxScalePercent = Config.Bind("Climax", "ScalePercent", 100f, "How strongly the climax gauge scales the vibrator output.");
            PistonWeakIntensityPercent = Config.Bind("Piston", "WeakIntensityPercent", 30f, "Coyote intensity used by the piston weak mode.");
            PistonMediumIntensityPercent = Config.Bind("Piston", "MediumIntensityPercent", 50f, "Coyote intensity used by the piston medium mode.");
            PistonStrongIntensityPercent = Config.Bind("Piston", "StrongIntensityPercent", 70f, "Coyote intensity used by the piston strong mode.");
            PistonRandomFallbackPercent = Config.Bind("Piston", "RandomFallbackPercent", 50f, "Fallback intensity while piston random mode has no effective speed yet.");
            ToggleKey = Config.Bind("Safety", "ToggleKey", "F8", "Keyboard key to toggle Armed on/off.");
            PanicKey = Config.Bind("Safety", "PanicKey", "F12", "Keyboard key to immediately disarm and send zero.");

            OutputMode = Config.Bind("Output", "OutputMode", "UdpJson", "LogOnly, UdpJson, or WebSocketJson. DryRun always forces LogOnly behavior.");
            UdpHost = Config.Bind("Output", "UdpHost", "127.0.0.1", "UDP bridge host.");
            UdpPort = Config.Bind("Output", "UdpPort", 39090, "UDP bridge port.");
            WebSocketUrl = Config.Bind("Output", "WebSocketUrl", "ws://127.0.0.1:39091/", "WebSocket bridge URL.");
            WebSocketTemplate = Config.Bind("Output", "WebSocketTemplate", "{\"type\":\"secretflashermanaka.vibrator\",\"channel\":\"{channel}\",\"intensity\":{intensity},\"vibratorOn\":{vibratorOn},\"configuredMode\":\"{configuredMode}\",\"effectiveMode\":\"{effectiveMode}\",\"rawStrength\":{rawStrength}}", "Template sent to a WebSocket bridge. Tokens: {channel}, {intensity}, {vibratorOn}, {configuredMode}, {effectiveMode}, {rawStrength}.");
            Channel = Config.Bind("Output", "Channel", "A", "Bridge channel label, for example A, B, or Both.");
            SendHz = Config.Bind("Output", "SendHz", 5f, "Maximum output update rate.");
            LogIntervalSeconds = Config.Bind("Output", "LogIntervalSeconds", 2f, "Minimum seconds between status log lines.");

            SuspicionDeadZone = Config.Bind("Mapping", "SuspicionDeadZone", 0f, "Suspicion at or below this value maps to zero.");
            SuspicionAtMax = Config.Bind("Mapping", "SuspicionAtMax", 100f, "Suspicion value that maps to MaxIntensityPercent.");
            FoundBoostPercent = Config.Bind("Mapping", "FoundBoostPercent", 0f, "Extra percent added while any NPC has found the player. Still capped by MaxIntensityPercent.");

            CoyoteLinkRuntime.Initialize();
            harmony = new Harmony("secretflashermanaka.coyotelink.vibrator");
            harmony.PatchAll();

            Logger.LogInfo("Secret Flasher Manaka Coyote Link loaded. DryRun=" + DryRun.Value + ", Armed=" + Armed.Value);
        }

        public override bool Unload()
        {
            try
            {
                if (harmony != null)
                {
                    harmony.UnpatchSelf();
                    harmony = null;
                }
            }
            catch
            {
            }

            CoyoteLinkRuntime.Shutdown();
            return true;
        }
    }

    [HarmonyPatch(typeof(CommonVibratorController), "Update")]
    internal static class CommonVibratorControllerUpdatePatch
    {
        public static void Postfix(CommonVibratorController __instance)
        {
            CoyoteLinkRuntime.UpdateVibrator(__instance);
        }
    }

    internal static class CoyoteLinkRuntime
    {
        private static readonly Stopwatch Clock = Stopwatch.StartNew();
        private static ICoyoteOutput output;
        private static string outputKey;
        private static double nextSendTime;
        private static double lastUpdateTime;
        private static double lastLogTime;
        private static float smoothedIntensity;
        private static bool previousToggleDown;
        private static bool previousPanicDown;

        public static void Initialize()
        {
            nextSendTime = 0.0;
            lastUpdateTime = NowSeconds();
            lastLogTime = 0.0;
            smoothedIntensity = 0f;
            previousToggleDown = false;
            previousPanicDown = false;
            EnsureOutput();
        }

        public static void Shutdown()
        {
            SafeZero("shutdown");
            if (output != null)
            {
                output.Dispose();
                output = null;
            }
        }

        public static void Update(NpcManager manager)
        {
            try
            {
                double now = NowSeconds();
                float deltaTime = (float)(now - lastUpdateTime);
                if (deltaTime < 0.001f || deltaTime > 1f)
                {
                    deltaTime = 0.016f;
                }
                lastUpdateTime = now;

                HandleHotkeys();
                EnsureOutput();

                SuspicionSnapshot snapshot = ReadSuspicion(manager);
                float target = MapIntensity(snapshot);
                smoothedIntensity = ApplyRamp(smoothedIntensity, target, deltaTime);

                float hz = Clamp(Plugin.SendHz.Value, 0.2f, 30f);
                if (now >= nextSendTime)
                {
                    nextSendTime = now + (1.0 / hz);
                    SendSnapshot(snapshot, smoothedIntensity, now);
                }
            }
            catch (Exception ex)
            {
                SafeZero("exception");
                Plugin.Logger.LogWarning("CoyoteLink update failed: " + ex.Message);
            }
        }

        public static void UpdateVibrator(CommonVibratorController controller)
        {
            try
            {
                double now = NowSeconds();
                float deltaTime = (float)(now - lastUpdateTime);
                if (deltaTime < 0.001f || deltaTime > 1f)
                {
                    deltaTime = 0.016f;
                }
                lastUpdateTime = now;

                HandleHotkeys();
                EnsureOutput();

                VibratorSnapshot snapshot = ReadVibrator(controller);
                float target = MapVibratorIntensity(snapshot);
                snapshot.PistonIntensityPercent = MapPistonIntensity(snapshot);
                smoothedIntensity = ApplyRamp(smoothedIntensity, target, deltaTime);

                float hz = Clamp(Plugin.SendHz.Value, 0.2f, 30f);
                if (now >= nextSendTime)
                {
                    nextSendTime = now + (1.0 / hz);
                    SendVibratorSnapshot(snapshot, smoothedIntensity, now);
                }
            }
            catch (Exception ex)
            {
                SafeZero("vibrator-exception");
                Plugin.Logger.LogWarning("CoyoteLink vibrator update failed: " + ex.Message);
            }
        }

        private static VibratorSnapshot ReadVibrator(CommonVibratorController controller)
        {
            VibratorSnapshot snapshot = new VibratorSnapshot();
            CommonVibratorController source = controller;

            try
            {
                if (CommonVibratorController.Leader != null)
                {
                    source = CommonVibratorController.Leader;
                }
            }
            catch
            {
            }

            if (source == null)
            {
                return snapshot;
            }

            snapshot.HasVibratorData = true;
            try
            {
                snapshot.ConfiguredMode = CommonVibratorController.VibrationStrength.ToString();
            }
            catch
            {
                snapshot.ConfiguredMode = "Unknown";
            }

            try
            {
                snapshot.EffectiveMode = source.currentMode.ToString();
            }
            catch
            {
                snapshot.EffectiveMode = snapshot.ConfiguredMode;
            }

            // BaibuStrength is a broken static IL2CPP proxy in this game build and
            // returns a native pointer-like garbage value. It is not used for the
            // mapping, so never expose it as telemetry.
            snapshot.RawStrength = 0;

            ReadClimax(snapshot);
            ReadPiston(snapshot);

            string configured = NormalizeMode(snapshot.ConfiguredMode);
            string effective = NormalizeMode(snapshot.EffectiveMode);
            snapshot.VibratorOn = configured != "off" && effective != "off";
            snapshot.VibratorStrong = effective == "high";
            return snapshot;
        }

        private static void ReadPiston(VibratorSnapshot snapshot)
        {
            try
            {
                PlayerController player = PlayerController.Instance;
                PistonMachineController piston = player == null || player.Pca == null
                    ? null
                    : player.Pca.PistonMachineController;
                if (piston == null)
                {
                    return;
                }

                snapshot.HasPistonData = true;
                snapshot.PistonEffectiveMode = piston.CurrentSpeedType;
                snapshot.PistonRandom = piston.isRandomMode;
                snapshot.PistonConfiguredMode = snapshot.PistonRandom ? 4 : snapshot.PistonEffectiveMode;
                snapshot.PistonOn = snapshot.PistonEffectiveMode > 0 || snapshot.PistonRandom;
                snapshot.PistonStrong = snapshot.PistonEffectiveMode >= 3;
            }
            catch
            {
                snapshot.HasPistonData = false;
                snapshot.PistonOn = false;
            }
        }

        private static void ReadClimax(VibratorSnapshot snapshot)
        {
            try
            {
                GameStateData gameStateData = GameState.GameStateData;
                if (gameStateData != null)
                {
                    snapshot.ClimaxValue = Math.Max(0f, gameStateData.PlayerEcstasy);
                    snapshot.HasClimaxData = true;
                }
                else
                {
                    snapshot.ClimaxValue = 0f;
                    snapshot.HasClimaxData = false;
                }
            }
            catch
            {
                snapshot.ClimaxValue = 0f;
                snapshot.HasClimaxData = false;
            }

            try
            {
                SexManager sexManager = SexManager.Instance;
                if (sexManager != null)
                {
                    snapshot.ClimaxActive = sexManager.IsJustEcstasy;
                }
            }
            catch
            {
                snapshot.ClimaxActive = false;
            }

            // IsJustEcstasy is only a short event flag. The sustained in-game
            // climax state is exposed by the player's state model.
            try
            {
                PlayerController player = PlayerController.Instance;
                if (player != null && player.Pca != null && player.Pca.PlayerState != null)
                {
                    snapshot.ClimaxActive = snapshot.ClimaxActive || player.Pca.PlayerState.IsEcstasyMotion;
                }
            }
            catch
            {
            }

            float gaugeMax = Clamp(Plugin.ClimaxGaugeMax.Value, 1f, 10000f);
            float climaxScale = Clamp(Plugin.ClimaxScalePercent.Value, 0f, 100f) / 100f;
            float rawClimaxPercent = 0f;
            if (snapshot.HasClimaxData)
            {
                // This game currently stores PlayerEcstasy as 0..1, while
                // older builds/configurations may expose a 0..100 value.
                rawClimaxPercent = snapshot.ClimaxValue <= 1f
                    ? snapshot.ClimaxValue * 100f
                    : snapshot.ClimaxValue * 100f / gaugeMax;
            }
            snapshot.ClimaxPercent = Clamp(rawClimaxPercent, 0f, 100f) * climaxScale;
            if (snapshot.ClimaxActive)
            {
                snapshot.ClimaxPercent = 100f;
            }
        }

        private static float MapVibratorIntensity(VibratorSnapshot snapshot)
        {
            if (!Plugin.Armed.Value || !snapshot.HasVibratorData || !snapshot.VibratorOn)
            {
                return 0f;
            }

            string configured = NormalizeMode(snapshot.ConfiguredMode);
            string effective = NormalizeMode(snapshot.EffectiveMode);

            if (configured == "off" || effective == "off")
            {
                return 0f;
            }
            float modeIntensity = 0f;
            if (effective == "low")
            {
                modeIntensity = Clamp(Plugin.VibratorLowIntensityPercent.Value, 0f, 100f);
            }
            else if (effective == "high")
            {
                modeIntensity = Clamp(Plugin.VibratorHighIntensityPercent.Value, 0f, 100f);
            }
            else if (effective == "random" || configured == "random")
            {
                modeIntensity = Clamp(Plugin.VibratorRandomFallbackPercent.Value, 0f, 100f);
            }

            return modeIntensity;
        }

        private static float MapPistonIntensity(VibratorSnapshot snapshot)
        {
            if (!Plugin.Armed.Value || !snapshot.HasPistonData || !snapshot.PistonOn)
            {
                return 0f;
            }

            float modeIntensity;
            if (snapshot.PistonRandom && snapshot.PistonEffectiveMode <= 0)
            {
                modeIntensity = Clamp(Plugin.PistonRandomFallbackPercent.Value, 0f, 100f);
            }
            else if (snapshot.PistonEffectiveMode <= 1)
            {
                modeIntensity = Clamp(Plugin.PistonWeakIntensityPercent.Value, 0f, 100f);
            }
            else if (snapshot.PistonEffectiveMode == 2)
            {
                modeIntensity = Clamp(Plugin.PistonMediumIntensityPercent.Value, 0f, 100f);
            }
            else
            {
                modeIntensity = Clamp(Plugin.PistonStrongIntensityPercent.Value, 0f, 100f);
            }

            return modeIntensity;
        }

        private static string NormalizeMode(string mode)
        {
            return (mode ?? string.Empty).Trim().ToLowerInvariant();
        }

        private static void HandleHotkeys()
        {
            bool toggleDown = IsKeyDown(Plugin.ToggleKey.Value);
            bool panicDown = IsKeyDown(Plugin.PanicKey.Value);

            if (toggleDown && !previousToggleDown)
            {
                Plugin.Armed.Value = !Plugin.Armed.Value;
                Plugin.Logger.LogInfo("CoyoteLink Armed=" + Plugin.Armed.Value);
                if (!Plugin.Armed.Value)
                {
                    smoothedIntensity = 0f;
                    SafeZero("disarmed");
                }
            }

            if (panicDown && !previousPanicDown)
            {
                Plugin.Armed.Value = false;
                smoothedIntensity = 0f;
                SafeZero("panic");
                Plugin.Logger.LogWarning("CoyoteLink panic stop: Armed=false, output=0");
            }

            previousToggleDown = toggleDown;
            previousPanicDown = panicDown;
        }

        private static void EnsureOutput()
        {
            string mode = Plugin.DryRun.Value ? "LogOnly" : Plugin.OutputMode.Value;
            string key = mode + "|" + Plugin.UdpHost.Value + "|" + Plugin.UdpPort.Value + "|" + Plugin.WebSocketUrl.Value;
            if (output != null && outputKey == key)
            {
                return;
            }

            if (output != null)
            {
                output.Dispose();
                output = null;
            }

            outputKey = key;
            string normalized = (mode ?? string.Empty).Trim().ToLowerInvariant();
            if (normalized == "udpjson")
            {
                output = new UdpJsonOutput(Plugin.UdpHost.Value, Plugin.UdpPort.Value);
            }
            else if (normalized == "websocketjson")
            {
                output = new WebSocketJsonOutput(Plugin.WebSocketUrl.Value);
            }
            else
            {
                output = new LogOnlyOutput();
            }
        }

        private static SuspicionSnapshot ReadSuspicion(NpcManager manager)
        {
            SuspicionSnapshot snapshot = new SuspicionSnapshot();
            if (manager == null)
            {
                return snapshot;
            }

            snapshot.HasNpcData = true;

            try
            {
                snapshot.ManagerMaxWatchingSuspicion = SafeFloat(manager.MaxWatchingNpcStrangeness);
                snapshot.ManagerSomeoneFound = manager.IsSomeOnePlayerFound;
                snapshot.ManagerSomeoneWatching = manager.IsWatchingStranger;
            }
            catch
            {
            }

            try
            {
                Il2CppSystem.Collections.Generic.List<NpcController> list = manager.ExistNpcList;
                if (list == null)
                {
                    return snapshot;
                }

                snapshot.NpcCount = list.Count;
                for (int i = 0; i < list.Count; i++)
                {
                    NpcController npc = list[i];
                    if (npc == null)
                    {
                        continue;
                    }

                    bool alive = true;
                    try
                    {
                        alive = npc.IsAlive;
                    }
                    catch
                    {
                    }

                    if (!alive)
                    {
                        continue;
                    }

                    snapshot.ActiveNpcCount++;

                    float suspicion = 0f;
                    try
                    {
                        suspicion = SafeFloat(npc.StrangenessValue);
                    }
                    catch
                    {
                        suspicion = 0f;
                    }

                    if (suspicion > snapshot.MaxSuspicion)
                    {
                        snapshot.MaxSuspicion = suspicion;
                    }
                    snapshot.NpcSuspicions.Add(suspicion);

                    try
                    {
                        if (npc.IsPlayerFound)
                        {
                            snapshot.FoundCount++;
                        }
                    }
                    catch
                    {
                    }

                    try
                    {
                        if (npc.IsWatchingStranger)
                        {
                            snapshot.WatchingCount++;
                        }
                    }
                    catch
                    {
                    }

                    try
                    {
                        if (npc.IsRiseStrangeness)
                        {
                            snapshot.RisingCount++;
                        }
                    }
                    catch
                    {
                    }
                }
            }
            catch (Exception ex)
            {
                snapshot.ReadError = ex.Message;
            }

            if (snapshot.ManagerMaxWatchingSuspicion > snapshot.MaxSuspicion)
            {
                snapshot.MaxSuspicion = snapshot.ManagerMaxWatchingSuspicion;
            }
            if (snapshot.ManagerSomeoneFound && snapshot.FoundCount == 0)
            {
                snapshot.FoundCount = 1;
            }

            return snapshot;
        }

        private static float MapIntensity(SuspicionSnapshot snapshot)
        {
            if (!Plugin.Armed.Value)
            {
                return 0f;
            }

            if (!snapshot.HasNpcData && Plugin.ZeroWhenNoNpc.Value)
            {
                return 0f;
            }

            float maxPercent = Clamp(Plugin.MaxIntensityPercent.Value, 0f, 100f);
            float low = Plugin.SuspicionDeadZone.Value;
            float high = Plugin.SuspicionAtMax.Value;
            if (high <= low + 0.001f)
            {
                high = low + 1f;
            }

            float t = (snapshot.MaxSuspicion - low) / (high - low);
            t = Clamp(t, 0f, 1f);

            float target = t * maxPercent;
            if (snapshot.FoundCount > 0)
            {
                target += Math.Max(0f, Plugin.FoundBoostPercent.Value);
            }

            return Clamp(target, 0f, maxPercent);
        }

        private static float ApplyRamp(float current, float target, float deltaTime)
        {
            float rate = Clamp(Plugin.MaxChangePercentPerSecond.Value, 1f, 100f);
            float maxStep = rate * Math.Max(0.001f, deltaTime);
            if (target > current + maxStep)
            {
                return current + maxStep;
            }
            if (target < current - maxStep)
            {
                return current - maxStep;
            }
            return target;
        }

        private static void SendSnapshot(SuspicionSnapshot snapshot, float intensity, double now)
        {
            int rounded = (int)Math.Round(Clamp(intensity, 0f, 100f));
            string json = JsonPayload.Build(snapshot, rounded, Plugin.Channel.Value);

            if (output != null)
            {
                output.Send(json);
            }

            if (now - lastLogTime >= Clamp(Plugin.LogIntervalSeconds.Value, 0.5f, 30f))
            {
                lastLogTime = now;
                Plugin.Logger.LogInfo("CoyoteLink suspicion=" + snapshot.MaxSuspicion.ToString("0.0", CultureInfo.InvariantCulture) +
                                      ", intensity=" + rounded +
                                      ", npcs=" + snapshot.ActiveNpcCount + "/" + snapshot.NpcCount +
                                      ", found=" + snapshot.FoundCount +
                                      ", armed=" + Plugin.Armed.Value +
                                      ", dryRun=" + Plugin.DryRun.Value);
            }
        }

        private static void SendVibratorSnapshot(VibratorSnapshot snapshot, float intensity, double now)
        {
            int rounded = (int)Math.Round(Clamp(intensity, 0f, 100f));
            string json = JsonPayload.Build(snapshot, rounded, Plugin.Channel.Value);

            if (output != null)
            {
                output.Send(json);
            }

            if (now - lastLogTime >= Clamp(Plugin.LogIntervalSeconds.Value, 0.5f, 30f))
            {
                lastLogTime = now;
                Plugin.Logger.LogInfo("CoyoteLink vibrator configured=" + snapshot.ConfiguredMode +
                                      ", effective=" + snapshot.EffectiveMode +
                                      ", rawStrength=" + snapshot.RawStrength +
                                      ", piston=" + snapshot.PistonConfiguredMode + "->" + snapshot.PistonEffectiveMode +
                                      ", pistonIntensity=" + snapshot.PistonIntensityPercent.ToString("0.0", CultureInfo.InvariantCulture) +
                                      ", climax=" + snapshot.ClimaxPercent.ToString("0.0", CultureInfo.InvariantCulture) +
                                      ", climaxActive=" + snapshot.ClimaxActive +
                                      ", intensity=" + rounded +
                                      ", on=" + snapshot.VibratorOn +
                                      ", armed=" + Plugin.Armed.Value +
                                      ", dryRun=" + Plugin.DryRun.Value);
            }
        }

        private static void SafeZero(string reason)
        {
            try
            {
                EnsureOutput();
                SuspicionSnapshot snapshot = new SuspicionSnapshot();
                snapshot.Reason = reason;
                if (output != null)
                {
                    output.Send(JsonPayload.Build(snapshot, 0, Plugin.Channel.Value, reason == "panic"));
                }
            }
            catch
            {
            }
        }

        internal static double NowSeconds()
        {
            return Clock.Elapsed.TotalSeconds;
        }

        private static float SafeFloat(float value)
        {
            if (float.IsNaN(value) || float.IsInfinity(value))
            {
                return 0f;
            }
            return value;
        }

        private static float Clamp(float value, float min, float max)
        {
            if (value < min)
            {
                return min;
            }
            if (value > max)
            {
                return max;
            }
            return value;
        }

        private static bool IsKeyDown(string keyName)
        {
            int vk = VirtualKeyCode(keyName);
            if (vk == 0)
            {
                return false;
            }
            return (GetAsyncKeyState(vk) & 0x8000) != 0;
        }

        private static int VirtualKeyCode(string keyName)
        {
            if (string.IsNullOrEmpty(keyName))
            {
                return 0;
            }

            string key = keyName.Trim().ToUpperInvariant();
            if (key.Length == 1)
            {
                char c = key[0];
                if (c >= 'A' && c <= 'Z')
                {
                    return (int)c;
                }
                if (c >= '0' && c <= '9')
                {
                    return (int)c;
                }
            }

            if (key.Length >= 2 && key[0] == 'F')
            {
                int number;
                if (int.TryParse(key.Substring(1), NumberStyles.Integer, CultureInfo.InvariantCulture, out number) &&
                    number >= 1 && number <= 24)
                {
                    return 0x70 + number - 1;
                }
            }

            if (key == "ESC" || key == "ESCAPE")
            {
                return 0x1B;
            }
            if (key == "SPACE")
            {
                return 0x20;
            }
            if (key == "PAUSE")
            {
                return 0x13;
            }

            return 0;
        }

        [DllImport("user32.dll")]
        private static extern short GetAsyncKeyState(int virtualKeyCode);
    }

    internal sealed class SuspicionSnapshot
    {
        public bool HasNpcData;
        public int NpcCount;
        public int ActiveNpcCount;
        public int FoundCount;
        public int WatchingCount;
        public int RisingCount;
        public List<float> NpcSuspicions = new List<float>();
        public float MaxSuspicion;
        public float ManagerMaxWatchingSuspicion;
        public bool ManagerSomeoneFound;
        public bool ManagerSomeoneWatching;
        public string ReadError;
        public string Reason = string.Empty;
    }

    internal sealed class VibratorSnapshot
    {
        public bool HasVibratorData;
        public bool VibratorOn;
        public bool VibratorStrong;
        public string ConfiguredMode = "Unknown";
        public string EffectiveMode = "Unknown";
        public int RawStrength;
        public bool HasClimaxData;
        public float ClimaxValue;
        public float ClimaxPercent;
        public bool ClimaxActive;
        public bool HasPistonData;
        public bool PistonOn;
        public bool PistonRandom;
        public int PistonConfiguredMode;
        public int PistonEffectiveMode;
        public float PistonIntensityPercent;
        public bool PistonStrong;
        public string Reason = string.Empty;
    }

    internal static class JsonPayload
    {
        public static string Build(SuspicionSnapshot snapshot, int intensity, string channel, bool panic = false)
        {
            string escapedChannel = Escape(channel ?? "A");
            string escapedReason = Escape(snapshot.Reason ?? string.Empty);
            string escapedError = Escape(snapshot.ReadError ?? string.Empty);
            string suspicion = snapshot.MaxSuspicion.ToString("0.###", CultureInfo.InvariantCulture);
            string managerSuspicion = snapshot.ManagerMaxWatchingSuspicion.ToString("0.###", CultureInfo.InvariantCulture);

            StringBuilder sb = new StringBuilder(256);
            sb.Append("{\"type\":\"secretflashermanaka.suspicion\",");
            sb.Append("\"channel\":\"").Append(escapedChannel).Append("\",");
            sb.Append("\"intensity\":").Append(intensity).Append(",");
            sb.Append("\"armed\":").Append(Plugin.Armed.Value ? "true" : "false").Append(",");
            sb.Append("\"panic\":").Append(panic ? "true" : "false").Append(",");
            sb.Append("\"suspicion\":").Append(suspicion).Append(",");
            sb.Append("\"managerSuspicion\":").Append(managerSuspicion).Append(",");
            sb.Append("\"npcCount\":").Append(snapshot.NpcCount).Append(",");
            sb.Append("\"activeNpcCount\":").Append(snapshot.ActiveNpcCount).Append(",");
            sb.Append("\"npcSuspicions\":[");
            for (int i = 0; i < snapshot.NpcSuspicions.Count; i++)
            {
                if (i > 0)
                {
                    sb.Append(",");
                }
                sb.Append(snapshot.NpcSuspicions[i].ToString("0.###", CultureInfo.InvariantCulture));
            }
            sb.Append("],");
            sb.Append("\"foundCount\":").Append(snapshot.FoundCount).Append(",");
            sb.Append("\"watchingCount\":").Append(snapshot.WatchingCount).Append(",");
            sb.Append("\"risingCount\":").Append(snapshot.RisingCount).Append(",");
            sb.Append("\"hasNpcData\":").Append(snapshot.HasNpcData ? "true" : "false").Append(",");
            sb.Append("\"managerSomeoneFound\":").Append(snapshot.ManagerSomeoneFound ? "true" : "false").Append(",");
            sb.Append("\"managerSomeoneWatching\":").Append(snapshot.ManagerSomeoneWatching ? "true" : "false").Append(",");
            sb.Append("\"reason\":\"").Append(escapedReason).Append("\",");
            sb.Append("\"error\":\"").Append(escapedError).Append("\"}");

            return ApplyTemplate(sb.ToString(), snapshot, intensity, channel);
        }

        public static string Build(VibratorSnapshot snapshot, int intensity, string channel, bool panic = false)
        {
            string escapedChannel = Escape(channel ?? "A");
            string escapedConfigured = Escape(snapshot.ConfiguredMode ?? "Unknown");
            string escapedEffective = Escape(snapshot.EffectiveMode ?? "Unknown");
            string escapedReason = Escape(snapshot.Reason ?? string.Empty);

            StringBuilder sb = new StringBuilder(256);
            sb.Append("{\"type\":\"secretflashermanaka.vibrator\",");
            sb.Append("\"channel\":\"").Append(escapedChannel).Append("\",");
            sb.Append("\"intensity\":").Append(intensity).Append(",");
            sb.Append("\"armed\":").Append(Plugin.Armed.Value ? "true" : "false").Append(",");
            sb.Append("\"panic\":").Append(panic ? "true" : "false").Append(",");
            sb.Append("\"vibratorOn\":").Append(snapshot.VibratorOn ? "true" : "false").Append(",");
            sb.Append("\"vibratorStrong\":").Append(snapshot.VibratorStrong ? "true" : "false").Append(",");
            sb.Append("\"configuredMode\":\"").Append(escapedConfigured).Append("\",");
            sb.Append("\"effectiveMode\":\"").Append(escapedEffective).Append("\",");
            sb.Append("\"rawStrength\":").Append(snapshot.RawStrength).Append(",");
            sb.Append("\"pistonOn\":").Append(snapshot.PistonOn ? "true" : "false").Append(",");
            sb.Append("\"pistonRandom\":").Append(snapshot.PistonRandom ? "true" : "false").Append(",");
            sb.Append("\"pistonConfiguredMode\":").Append(snapshot.PistonConfiguredMode).Append(",");
            sb.Append("\"pistonEffectiveMode\":").Append(snapshot.PistonEffectiveMode).Append(",");
            sb.Append("\"pistonIntensity\":").Append(snapshot.PistonIntensityPercent.ToString("0.###", CultureInfo.InvariantCulture)).Append(",");
            sb.Append("\"pistonStrong\":").Append(snapshot.PistonStrong ? "true" : "false").Append(",");
            sb.Append("\"hasPistonData\":").Append(snapshot.HasPistonData ? "true" : "false").Append(",");
            sb.Append("\"climaxValue\":").Append(snapshot.ClimaxValue.ToString("0.###", CultureInfo.InvariantCulture)).Append(",");
            sb.Append("\"climaxPercent\":").Append(snapshot.ClimaxPercent.ToString("0.###", CultureInfo.InvariantCulture)).Append(",");
            sb.Append("\"climaxActive\":").Append(snapshot.ClimaxActive ? "true" : "false").Append(",");
            sb.Append("\"hasClimaxData\":").Append(snapshot.HasClimaxData ? "true" : "false").Append(",");
            sb.Append("\"hasVibratorData\":").Append(snapshot.HasVibratorData ? "true" : "false").Append(",");
            sb.Append("\"reason\":\"").Append(escapedReason).Append("\"}");

            return ApplyVibratorTemplate(sb.ToString(), snapshot, intensity, channel);
        }

        private static string ApplyTemplate(string defaultJson, SuspicionSnapshot snapshot, int intensity, string channel)
        {
            string template = Plugin.WebSocketTemplate.Value;
            if (string.IsNullOrEmpty(template))
            {
                return defaultJson;
            }

            string normalizedMode = Plugin.OutputMode.Value == null ? string.Empty : Plugin.OutputMode.Value.Trim().ToLowerInvariant();
            if (normalizedMode != "websocketjson")
            {
                return defaultJson;
            }

            string result = template;
            result = result.Replace("{channel}", Escape(channel ?? "A"));
            result = result.Replace("{intensity}", intensity.ToString(CultureInfo.InvariantCulture));
            result = result.Replace("{suspicion}", snapshot.MaxSuspicion.ToString("0.###", CultureInfo.InvariantCulture));
            result = result.Replace("{found}", snapshot.FoundCount > 0 ? "true" : "false");
            result = result.Replace("{npcCount}", snapshot.NpcCount.ToString(CultureInfo.InvariantCulture));
            result = result.Replace("{activeNpcCount}", snapshot.ActiveNpcCount.ToString(CultureInfo.InvariantCulture));
            return result;
        }

        private static string ApplyVibratorTemplate(string defaultJson, VibratorSnapshot snapshot, int intensity, string channel)
        {
            string template = Plugin.WebSocketTemplate.Value;
            if (string.IsNullOrEmpty(template) ||
                template.IndexOf("{suspicion}", StringComparison.Ordinal) >= 0 ||
                template.IndexOf("{found}", StringComparison.Ordinal) >= 0)
            {
                return defaultJson;
            }

            string normalizedMode = Plugin.OutputMode.Value == null ? string.Empty : Plugin.OutputMode.Value.Trim().ToLowerInvariant();
            if (normalizedMode != "websocketjson")
            {
                return defaultJson;
            }

            string result = template;
            result = result.Replace("{channel}", Escape(channel ?? "A"));
            result = result.Replace("{intensity}", intensity.ToString(CultureInfo.InvariantCulture));
            result = result.Replace("{vibratorOn}", snapshot.VibratorOn ? "true" : "false");
            result = result.Replace("{configuredMode}", Escape(snapshot.ConfiguredMode ?? "Unknown"));
            result = result.Replace("{effectiveMode}", Escape(snapshot.EffectiveMode ?? "Unknown"));
            result = result.Replace("{rawStrength}", snapshot.RawStrength.ToString(CultureInfo.InvariantCulture));
            return result;
        }

        private static string Escape(string value)
        {
            return value.Replace("\\", "\\\\").Replace("\"", "\\\"");
        }
    }

    internal interface ICoyoteOutput : IDisposable
    {
        void Send(string payload);
    }

    internal sealed class LogOnlyOutput : ICoyoteOutput
    {
        public void Send(string payload)
        {
        }

        public void Dispose()
        {
        }
    }

    internal sealed class UdpJsonOutput : ICoyoteOutput
    {
        private readonly UdpClient client;

        public UdpJsonOutput(string host, int port)
        {
            client = new UdpClient();
            client.Connect(host, port);
            Plugin.Logger.LogInfo("CoyoteLink UDP output connected to " + host + ":" + port);
        }

        public void Send(string payload)
        {
            byte[] data = Encoding.UTF8.GetBytes(payload);
            client.Send(data, data.Length);
        }

        public void Dispose()
        {
            client.Close();
        }
    }

    internal sealed class WebSocketJsonOutput : ICoyoteOutput
    {
        private readonly Uri uri;
        private ClientWebSocket socket;
        private double nextReconnectTime;

        public WebSocketJsonOutput(string url)
        {
            uri = new Uri(url);
        }

        public void Send(string payload)
        {
            if (!EnsureConnected())
            {
                return;
            }

            try
            {
                byte[] data = Encoding.UTF8.GetBytes(payload);
                socket.SendAsync(new ArraySegment<byte>(data), WebSocketMessageType.Text, true, CancellationToken.None).Wait(100);
            }
            catch (Exception ex)
            {
                Plugin.Logger.LogWarning("CoyoteLink WebSocket send failed: " + ex.Message);
                DisposeSocket();
            }
        }

        public void Dispose()
        {
            DisposeSocket();
        }

        private bool EnsureConnected()
        {
            if (socket != null && socket.State == WebSocketState.Open)
            {
                return true;
            }

            double now = CoyoteLinkRuntime.NowSeconds();
            if (now < nextReconnectTime)
            {
                return false;
            }

            nextReconnectTime = now + 3.0;
            DisposeSocket();

            try
            {
                socket = new ClientWebSocket();
                if (!socket.ConnectAsync(uri, CancellationToken.None).Wait(500))
                {
                    Plugin.Logger.LogWarning("CoyoteLink WebSocket connect timed out: " + uri);
                    DisposeSocket();
                    return false;
                }

                Plugin.Logger.LogInfo("CoyoteLink WebSocket output connected to " + uri);
                return socket.State == WebSocketState.Open;
            }
            catch (Exception ex)
            {
                Plugin.Logger.LogWarning("CoyoteLink WebSocket connect failed: " + ex.Message);
                DisposeSocket();
                return false;
            }
        }

        private void DisposeSocket()
        {
            try
            {
                if (socket != null)
                {
                    socket.Dispose();
                }
            }
            catch
            {
            }
            socket = null;
        }
    }
}
