"""GUI 入口（打包为无控制台的托盘 exe）：遥控器面板。"""

if __name__ == "__main__":
    import sys
    if '--enhanced-worker' in sys.argv:
        from miao.enhancement import worker_main
        raise SystemExit(worker_main())
    else:
        from miao.remote_panel import run_panel
        run_panel()
