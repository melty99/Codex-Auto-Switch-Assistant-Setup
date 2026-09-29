// Direct MSAA avoids UI Automation's foreground activation on WebView2 Invoke.
// Only the uniquely identified account's Refresh button may be invoked.
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using Accessibility;

public sealed class QuotaGuardQuietRefresh : IDisposable {
    [DllImport("oleacc.dll")]
    private static extern int AccessibleObjectFromWindow(IntPtr hwnd, uint id, ref Guid iid,
        [MarshalAs(UnmanagedType.Interface)] out IAccessible value);
    [DllImport("oleacc.dll")]
    private static extern int AccessibleChildren(IAccessible container, int start, int count,
        [Out, MarshalAs(UnmanagedType.LPArray, SizeParamIndex=2)] object[] children, out int actual);
    private readonly HashSet<object> owned = new HashSet<object>();

    private IAccessible Own(object value) {
        if (value != null && Marshal.IsComObject(value)) owned.Add(value);
        return value as IAccessible;
    }
    private List<IAccessible> Find(IAccessible root, string name, int role, int depth, ref int budget) {
        if (depth > 24 || --budget < 0) throw new InvalidOperationException("Accessibility tree exceeds limit");
        var result = new List<IAccessible>();
        if (root.get_accName(0) == name && Convert.ToInt32(root.get_accRole(0)) == role) result.Add(root);
        int count = root.accChildCount;
        if (count < 0 || count > 512) throw new InvalidOperationException("Invalid accessible child count");
        if (count == 0) return result;
        var children = new object[count]; int actual;
        Marshal.ThrowExceptionForHR(AccessibleChildren(root, 0, count, children, out actual));
        foreach (var value in children) Own(value);
        for (int i = 0; i < actual; i++) {
            var child = children[i] as IAccessible;
            if (child != null) result.AddRange(Find(child, name, role, depth + 1, ref budget));
        }
        return result;
    }
    private List<IAccessible> Find(IAccessible root, string name, int role) {
        int budget = 2048;
        return Find(root, name, role, 0, ref budget);
    }
    private void Refresh(IntPtr renderer, string account) {
        var iid = new Guid("618736e0-3c3d-11cf-810c-00aa00389b71");
        IAccessible root;
        Marshal.ThrowExceptionForHR(AccessibleObjectFromWindow(renderer, 0xfffffffcu, ref iid, out root));
        Own(root);
        if (root == null) throw new InvalidOperationException("Renderer accessibility is unavailable");
        var titles = Find(root, account, 41); // ROLE_SYSTEM_STATICTEXT
        if (titles.Count != 1) throw new InvalidOperationException("Account title is not unique");
        var card = titles[0];
        for (int i = 0; i < 8 && card != root; i++) {
            card = Own(card.accParent);
            if (card == null || card == root) break;
            var refresh = Find(card, "刷新", 43); // ROLE_SYSTEM_PUSHBUTTON
            if (refresh.Count > 1) break;
            if (refresh.Count == 0) continue;
            if (Find(card, account, 41).Count != 1 ||
                Find(card, "使用中", 43).Count + Find(card, "启用", 43).Count != 1)
                throw new InvalidOperationException("Account card identity is ambiguous");
            var button = refresh[0];
            if ((Convert.ToInt32(button.get_accState(0)) & 1) != 0)
                throw new InvalidOperationException("Refresh is disabled");
            // No focus, foreground, screen-coordinate, or keyboard operations.
            button.accDoDefaultAction(0);
            return;
        }
        throw new InvalidOperationException("No unique account Refresh button");
    }
    public static void Run(IntPtr renderer, string account) {
        using (var operation = new QuotaGuardQuietRefresh()) operation.Refresh(renderer, account);
    }
    public void Dispose() {
        foreach (var value in owned) Marshal.ReleaseComObject(value);
        owned.Clear();
    }
}
