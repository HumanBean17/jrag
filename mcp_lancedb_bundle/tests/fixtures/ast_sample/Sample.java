package com.example.app;

/**
 * Small fixture: inheritance, interface, inner type, autowired, DI to same file type.
 */
public class Sample extends BaseSample implements IFace {
    @org.springframework.beans.factory.annotation.Autowired
    private Dep dep;

    public class Inner { }

    public void m() { }
}

interface IFace { }

class BaseSample { }

class Dep { }
