"""
CST COM 接口诊断脚本
运行: python scripts/diagnose.py
把输出截图发给 Claude，用于确定正确的 API 路径
"""
import win32com.client
import win32com.client.gencache

def show(label, obj, depth=0):
    indent = "  " * depth
    if obj is None:
        print(f"{indent}[{label}] = None")
        return
    print(f"{indent}[{label}] type={type(obj).__name__}  hasAddToHistory={hasattr(obj, 'AddToHistory')}")
    if depth >= 2:
        return
    try:
        attrs = [a for a in dir(obj) if not a.startswith("_")]
        print(f"{indent}  attrs: {attrs[:30]}")
    except Exception as e:
        print(f"{indent}  cannot list attrs: {e}")

try:
    print("=== Connecting to CST ===")
    app = win32com.client.GetActiveObject("CSTStudio.Application")
    print(f"app type: {type(app).__name__}")

    print("\n=== App attributes ===")
    try:
        attrs = [a for a in dir(app) if not a.startswith("_")]
        print(f"All: {attrs}")
    except Exception as e:
        print(f"Cannot list: {e}")

    print("\n=== GetActiveProject() ===")
    try:
        proj = app.GetActiveProject()
        show("proj", proj)
        # Navigate sub-objects
        for sub in ["Active3D", "Design", "GetDesign", "ActiveDesign",
                    "Modeler", "GetModeler", "Application", "GetApplication"]:
            try:
                fn = getattr(proj, sub, None)
                if fn is None:
                    continue
                result = fn() if callable(fn) else fn
                show(f"proj.{sub}", result, 1)
                if hasattr(result, "AddToHistory"):
                    print(f"  *** FOUND AddToHistory at proj.{sub} ***")
            except Exception as e:
                print(f"  proj.{sub}: {e}")
    except Exception as e:
        print(f"GetActiveProject() failed: {e}")

    print("\n=== Active3D raw COM dispatch ===")
    try:
        import pythoncom
        oleobj = app._oleobj_
        # Try different GetIDsOfNames calling conventions
        dispid = None
        try:
            ids = oleobj.GetIDsOfNames(pythoncom.IID_NULL, "Active3D")
            dispid = ids[0] if isinstance(ids, (list, tuple)) else ids
        except Exception as e1:
            try:
                ids = oleobj.GetIDsOfNames("Active3D")
                dispid = ids[0] if isinstance(ids, (list, tuple)) else ids
            except Exception as e2:
                print(f"GetIDsOfNames failed: {e1} | {e2}")

        if dispid is not None:
            print(f"Active3D DISPID: {hex(dispid)}")
            for flag in [pythoncom.DISPATCH_METHOD, pythoncom.DISPATCH_PROPERTYGET,
                         pythoncom.DISPATCH_METHOD | pythoncom.DISPATCH_PROPERTYGET]:
                for args in [(), (0,), (0, 0), (1,), (1, 0)]:
                    try:
                        raw = oleobj.Invoke(dispid, 0, flag, True, *args)
                        obj2 = win32com.client.Dispatch(raw)
                        print(f"  Invoke(flag={flag}, args={args}) -> {type(obj2).__name__} hasATH={hasattr(obj2, 'AddToHistory')}")
                        if hasattr(obj2, "AddToHistory"):
                            print(f"  *** FOUND via raw dispatch (flag={flag}, args={args}) ***")
                            break
                    except Exception as ex:
                        print(f"  Invoke(flag={flag}, args={args}) -> {str(ex)[:80]}")
    except Exception as e:
        print(f"Raw dispatch setup failed: {e}")

    print("\n=== gen_py cache path ===")
    try:
        print(win32com.client.gencache.GetGeneratePath())
    except Exception as e:
        print(f"Cannot get path: {e}")

except Exception as e:
    print(f"Connection failed: {e}")
